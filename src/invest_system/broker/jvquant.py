"""jvQuant OpenAPI 实盘交易执行器。

文档：http://jvquant.com/wiki.html
支持券商：个人账户仅东方财富，机构账户无限制。

配置项（.env）：
    JVQUANT_TOKEN=your_token        # jvQuant API token
    JVQUANT_ACCOUNT=12位资金账号      # 券商资金账号
    JVQUANT_PASSWORD=交易密码         # 资金交易密码
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

import httpx

from invest_system.broker import Broker, OrderResult

if TYPE_CHECKING:
    from invest_system.config import Settings
    from invest_system.portfolio import Portfolio

log = logging.getLogger("broker.jvquant")

SH_TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class JvQuantSession:
    token: str
    ticket: str
    expire_at: float
    trade_server: str


class JvQuantBroker(Broker):
    def __init__(self, portfolio: "Portfolio", settings: "Settings"):
        super().__init__(portfolio, settings)
        self.token = getattr(settings, "jvquant_token", "").strip()
        self.account = getattr(settings, "jvquant_account", "").strip()
        self.password = getattr(settings, "jvquant_password", "").strip()
        self._session: JvQuantSession | None = None
        self._trade_server: str | None = None
        self._stock_names: dict[str, str] = {}

    def _get_trade_server(self) -> str:
        if self._trade_server:
            return self._trade_server
        resp = httpx.get(
            "http://jvquant.com/server",
            params={"market": "ab", "type": "trade", "token": self.token},
            timeout=10.0,
        )
        data = resp.json()
        if data.get("code") != "0":
            raise RuntimeError(f"获取交易服务器失败: {data}")
        self._trade_server = data["server"]
        log.info("jvQuant trade server: %s", self._trade_server)
        return self._trade_server

    def _ensure_session(self) -> JvQuantSession:
        now = time.time()
        if self._session and self._session.expire_at > now + 60:
            return self._session
        server = self._get_trade_server()
        resp = httpx.post(
            f"http://{server}/login",
            data={"token": self.token, "acc": self.account, "pass": self.password},
            timeout=15.0,
        )
        data = resp.json()
        if data.get("code") != "0":
            raise RuntimeError(f"登录柜台失败: {data}")
        ticket = data["ticket"]
        expire = int(data.get("expire", 3600))
        self._session = JvQuantSession(
            token=self.token,
            ticket=ticket,
            expire_at=now + expire,
            trade_server=server,
        )
        log.info("jvQuant 登录成功，ticket 有效期 %ds", expire)
        return self._session

    def _get_stock_name(self, symbol: str) -> str:
        symbol = symbol.upper().strip()
        if symbol in self._stock_names:
            return self._stock_names[symbol]
        code = symbol.split(".")[0]
        if code.isdigit() and len(code) == 6:
            self._stock_names[symbol] = code
            return code
        return symbol

    def execute(
        self,
        side: Literal["buy", "sell"],
        symbol: str,
        shares: float,
        price: float,
        *,
        day: date | None = None,
        ts: datetime | None = None,
    ) -> OrderResult:
        try:
            sess = self._ensure_session()
        except Exception as e:
            log.exception("获取 session 失败")
            return OrderResult(
                success=False,
                symbol=symbol,
                side=side,
                shares=shares,
                price=price,
                fee=0.0,
                message=f"登录失败: {e}",
            )
        trade_type = "buy" if side == "buy" else "sale"
        code = symbol.upper().split(".")[0]
        name = self._get_stock_name(symbol)
        try:
            resp = httpx.post(
                f"http://{sess.trade_server}/order",
                data={
                    "trade": trade_type,
                    "token": self.token,
                    "ticket": sess.ticket,
                    "code": code,
                    "name": name,
                    "price": f"{price:.2f}",
                    "volume": int(shares),
                },
                timeout=15.0,
            )
            data = resp.json()
            order_id = data.get("order_id")
            msg = data.get("message", "")
            if order_id:
                log.info("委托成功: %s %s %d@%.2f order_id=%s", side, symbol, shares, price, order_id)
                return OrderResult(
                    success=True,
                    symbol=symbol,
                    side=side,
                    shares=shares,
                    price=price,
                    fee=shares * price * self.portfolio.fee_rate,
                    message=msg or "委托成功",
                    order_id=order_id,
                )
            log.warning("委托失败: %s", msg)
            return OrderResult(
                success=False,
                symbol=symbol,
                side=side,
                shares=shares,
                price=price,
                fee=0.0,
                message=msg or "委托失败",
            )
        except Exception as e:
            log.exception("下单异常")
            return OrderResult(
                success=False,
                symbol=symbol,
                side=side,
                shares=shares,
                price=price,
                fee=0.0,
                message=f"下单异常: {e}",
            )

    def get_positions(self) -> dict[str, float]:
        try:
            sess = self._ensure_session()
            resp = httpx.post(
                f"http://{sess.trade_server}/hold",
                data={"token": self.token, "ticket": sess.ticket},
                timeout=10.0,
            )
            data = resp.json()
            positions: dict[str, float] = {}
            for item in data.get("hold_list", []):
                code = str(item.get("code", "")).upper()
                vol = float(item.get("hold_vol", 0))
                if code and vol > 0:
                    positions[code] = vol
            return positions
        except Exception:
            log.exception("查询持仓失败")
            return dict(self.portfolio.positions)

    def get_cash(self) -> float:
        try:
            sess = self._ensure_session()
            resp = httpx.post(
                f"http://{sess.trade_server}/hold",
                data={"token": self.token, "ticket": sess.ticket},
                timeout=10.0,
            )
            data = resp.json()
            return float(data.get("usable", 0))
        except Exception:
            log.exception("查询资金失败")
            return self.portfolio.cash

    def query_orders(self) -> list[dict]:
        try:
            sess = self._ensure_session()
            resp = httpx.post(
                f"http://{sess.trade_server}/trade",
                data={"token": self.token, "ticket": sess.ticket},
                timeout=10.0,
            )
            data = resp.json()
            return data.get("list", [])
        except Exception:
            log.exception("查询委托失败")
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            sess = self._ensure_session()
            resp = httpx.post(
                f"http://{sess.trade_server}/cancel",
                data={"token": self.token, "ticket": sess.ticket, "order_id": order_id},
                timeout=10.0,
            )
            data = resp.json()
            if data.get("code") == "0":
                log.info("撤单成功: %s", order_id)
                return True
            log.warning("撤单失败: %s", data.get("message"))
            return False
        except Exception:
            log.exception("撤单异常")
            return False
