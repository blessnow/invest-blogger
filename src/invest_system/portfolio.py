from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal


Side = Literal["buy", "sell"]


@dataclass
class Transaction:
    day: date
    symbol: str
    side: Side
    shares: float
    price: float
    fee: float
    cash_after: float
    avg_cost_before: float | None = None
    realized_pnl: float | None = None
    #: ISO-8601 时间戳（含时区，例如 "2026-05-09T11:30:14+08:00"）。
    #: 历史记录可能为 None；序列化/排序时缺失视作 day 当天 15:00 本地时区。
    timestamp: str | None = None


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, float] = field(default_factory=dict)
    transactions: list[Transaction] = field(default_factory=list)
    fee_rate: float = 0.0005
    avg_cost: dict[str, float] = field(default_factory=dict)

    def copy_state(self) -> tuple[float, dict[str, float]]:
        return self.cash, dict(self.positions)

    def equity(self, prices: dict[str, float]) -> float:
        total = self.cash
        for sym, qty in self.positions.items():
            p = prices.get(sym)
            if p is not None and qty:
                total += qty * p
        return total

    def market_value(self, symbol: str, prices: dict[str, float]) -> float:
        qty = self.positions.get(symbol, 0.0)
        p = prices.get(symbol)
        if p is None or not qty:
            return 0.0
        return qty * p

    def buy(
        self,
        day: date,
        symbol: str,
        shares: float,
        price: float,
        *,
        ts: datetime | None = None,
    ) -> bool:
        if shares <= 0:
            return False
        cost = shares * price
        fee = cost * self.fee_rate
        total = cost + fee
        if total > self.cash + 1e-9:
            return False
        self.cash -= total
        old_qty = self.positions.get(symbol, 0.0)
        old_avg = self.avg_cost.get(symbol, 0.0) if old_qty > 0 else 0.0
        new_qty = old_qty + shares
        # 含手续费的加权平均买入成本（每股）
        new_avg = (old_qty * old_avg + total) / new_qty if new_qty > 0 else 0.0
        self.positions[symbol] = new_qty
        self.avg_cost[symbol] = new_avg
        self.transactions.append(
            Transaction(
                day=day,
                symbol=symbol,
                side="buy",
                shares=shares,
                price=price,
                fee=fee,
                cash_after=self.cash,
                avg_cost_before=old_avg if old_qty > 0 else None,
                realized_pnl=None,
                timestamp=ts.isoformat(timespec="seconds") if ts is not None else None,
            )
        )
        return True

    def sell(
        self,
        day: date,
        symbol: str,
        shares: float,
        price: float,
        *,
        ts: datetime | None = None,
    ) -> bool:
        if shares <= 0:
            return False
        held = self.positions.get(symbol, 0.0)
        if shares > held + 1e-9:
            return False
        proceeds = shares * price
        fee = proceeds * self.fee_rate
        self.cash += proceeds - fee
        avg = self.avg_cost.get(symbol, 0.0)
        # 已实现盈亏 = 卖出净流入(扣手续费) - 卖出股数 × 平均成本
        realized = (proceeds - fee) - shares * avg if avg > 0 else None
        new_h = held - shares
        if new_h <= 1e-9:
            self.positions.pop(symbol, None)
            self.avg_cost.pop(symbol, None)
        else:
            self.positions[symbol] = new_h
            # 平均成本对未平仓部分保持不变
        self.transactions.append(
            Transaction(
                day=day,
                symbol=symbol,
                side="sell",
                shares=shares,
                price=price,
                fee=fee,
                cash_after=self.cash,
                avg_cost_before=avg if avg > 0 else None,
                realized_pnl=realized,
                timestamp=ts.isoformat(timespec="seconds") if ts is not None else None,
            )
        )
        return True
