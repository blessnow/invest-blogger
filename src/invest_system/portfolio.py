from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
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


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, float] = field(default_factory=dict)
    transactions: list[Transaction] = field(default_factory=list)
    fee_rate: float = 0.0005

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

    def buy(self, day: date, symbol: str, shares: float, price: float) -> bool:
        if shares <= 0:
            return False
        cost = shares * price
        fee = cost * self.fee_rate
        total = cost + fee
        if total > self.cash + 1e-9:
            return False
        self.cash -= total
        self.positions[symbol] = self.positions.get(symbol, 0.0) + shares
        self.transactions.append(
            Transaction(
                day=day,
                symbol=symbol,
                side="buy",
                shares=shares,
                price=price,
                fee=fee,
                cash_after=self.cash,
            )
        )
        return True

    def sell(self, day: date, symbol: str, shares: float, price: float) -> bool:
        if shares <= 0:
            return False
        held = self.positions.get(symbol, 0.0)
        if shares > held + 1e-9:
            return False
        proceeds = shares * price
        fee = proceeds * self.fee_rate
        self.cash += proceeds - fee
        new_h = held - shares
        if new_h <= 1e-9:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = new_h
        self.transactions.append(
            Transaction(
                day=day,
                symbol=symbol,
                side="sell",
                shares=shares,
                price=price,
                fee=fee,
                cash_after=self.cash,
            )
        )
        return True
