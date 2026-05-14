"""模拟交易执行器：仅在内存中更新 Portfolio，不发送真实订单。"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Literal

from invest_system.broker import Broker, OrderResult

if TYPE_CHECKING:
    from invest_system.config import Settings
    from invest_system.portfolio import Portfolio


class PaperBroker(Broker):
    """模拟盘执行器：复用 Portfolio 的 buy/sell 方法。"""

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
        if day is None:
            day = date.today()
        symbol = symbol.upper().strip()
        if side == "buy":
            ok = self.portfolio.buy(day, symbol, shares, price, ts=ts)
            if ok:
                return OrderResult(
                    success=True,
                    symbol=symbol,
                    side=side,
                    shares=shares,
                    price=price,
                    fee=shares * price * self.portfolio.fee_rate,
                    message="模拟买入成功",
                )
            return OrderResult(
                success=False,
                symbol=symbol,
                side=side,
                shares=shares,
                price=price,
                fee=0.0,
                message="模拟买入失败（资金不足或其他原因）",
            )
        if side == "sell":
            ok = self.portfolio.sell(day, symbol, shares, price, ts=ts)
            if ok:
                return OrderResult(
                    success=True,
                    symbol=symbol,
                    side=side,
                    shares=shares,
                    price=price,
                    fee=shares * price * self.portfolio.fee_rate,
                    message="模拟卖出成功",
                )
            return OrderResult(
                success=False,
                symbol=symbol,
                side=side,
                shares=shares,
                price=price,
                fee=0.0,
                message="模拟卖出失败（持仓不足或 T+1 限制）",
            )
        return OrderResult(
            success=False,
            symbol=symbol,
            side=side,
            shares=shares,
            price=price,
            fee=0.0,
            message=f"未知交易方向: {side}",
        )

    def get_positions(self) -> dict[str, float]:
        return dict(self.portfolio.positions)

    def get_cash(self) -> float:
        return self.portfolio.cash
