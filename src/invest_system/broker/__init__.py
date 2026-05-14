"""交易执行器抽象层：支持模拟盘和实盘切换。

使用方式：
    from invest_system.broker import create_broker
    
    broker = create_broker("paper", portfolio, settings)  # 模拟盘
    broker = create_broker("gm", portfolio, settings)     # 掘金实盘
    
    result = broker.execute("buy", "600519.SS", 100, 1850.0)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from invest_system.config import Settings
    from invest_system.portfolio import Portfolio

BrokerMode = Literal["paper", "gm", "jvquant"]


@dataclass
class OrderResult:
    success: bool
    symbol: str
    side: str
    shares: float
    price: float
    fee: float
    message: str = ""
    order_id: str | None = None


class Broker(ABC):
    """交易执行器基类。"""

    def __init__(self, portfolio: "Portfolio", settings: "Settings"):
        self.portfolio = portfolio
        self.settings = settings

    @abstractmethod
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
        """执行交易指令。"""
        ...

    @abstractmethod
    def get_positions(self) -> dict[str, float]:
        """获取当前持仓。"""
        ...

    @abstractmethod
    def get_cash(self) -> float:
        """获取可用资金。"""
        ...


def create_broker(
    mode: BrokerMode,
    portfolio: "Portfolio",
    settings: "Settings",
) -> Broker:
    """工厂函数：根据模式创建对应的交易执行器。"""
    if mode == "paper":
        from invest_system.broker.paper import PaperBroker

        return PaperBroker(portfolio, settings)
    if mode == "gm":
        from invest_system.broker.gm import GmBroker

        return GmBroker(portfolio, settings)
    if mode == "jvquant":
        from invest_system.broker.jvquant import JvQuantBroker

        return JvQuantBroker(portfolio, settings)
    raise ValueError(f"Unknown broker mode: {mode}")
