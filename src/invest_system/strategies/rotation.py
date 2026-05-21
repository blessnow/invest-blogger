"""rotation 策略 — 行业 ETF 轮动（长期稳健派）。

设计哲学（彻底放弃单股投机）：
  - 候选 = 31 个行业 ETF（半导体/军工/医药/新能源/银行/酒等，本地 etf_daily.csv）
  - 每 N 个交易日（默认 5 = 周一）筛选近 X 日最强 K 个行业
  - 等权持有，每只 1/K 仓位
  - 大盘择时：HS300 < MA60 全空仓
  - 个股止损：ETF 跌 -8% 平仓

优点：
  - ETF 不暴雷、不一字涨停、撮合真实
  - 30+ 行业总有几个在风口
  - 回撤可控、长期可复利

输出格式：list[dict] action，与 LLM 兼容，可由 _apply_actions 执行。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from invest_system.config import Settings


# 31 个行业 ETF（来自本地 etf_daily.csv），Yahoo 代码格式
ETF_UNIVERSE: list[str] = [
    "512480.SS",  # 半导体
    "515230.SS",  # 软件
    "515880.SS",  # 通信
    "512720.SS",  # 计算机
    "512980.SS",  # 传媒
    "512010.SS",  # 医药
    "512290.SS",  # 生物医药
    "512170.SS",  # 医疗
    "515120.SS",  # 创新药
    "515030.SS",  # 新能源车
    "515790.SS",  # 光伏
    "561560.SS",  # 电力
    "516110.SS",  # 汽车
    "159995.SZ",  # 高端装备
    "560280.SS",  # 工程机械
    "512660.SS",  # 军工
    "516020.SS",  # 化工
    "512400.SS",  # 有色
    "159731.SZ",  # 石化
    "515220.SS",  # 煤炭
    "512690.SS",  # 酒
    "515170.SS",  # 食品饮料
    "159996.SZ",  # 家电
    "510150.SS",  # 消费
    "512880.SS",  # 证券
    "512800.SS",  # 银行
    "512200.SS",  # 房地产
    "516950.SS",  # 基建
    "512580.SS",  # 环保
    # 510300/510500 留作基准
]


@dataclass
class RotationParams:
    top_k: int = 3                       # 持仓数（行业 ETF）
    momentum_window: int = 20            # 评分用动量窗口
    rebalance_every_days: int = 5        # 调仓周期（5 = 周度）
    stop_loss_pct: float = 8.0           # 个股止损
    deploy_fraction: float = 0.95
    # 大盘择时
    regime_symbol: str = "000300.SS"
    regime_ma_window: int = 60
    regime_enabled: bool = True
    # 趋势确认
    require_uptrend: bool = True         # ETF 当前价 > MA20 才纳入
    uptrend_ma_window: int = 20


def params_from_settings(settings: Settings) -> RotationParams:
    def _f(name: str, default: float) -> float:
        v = getattr(settings, name, default)
        try: return float(v)
        except: return default
    def _i(name: str, default: int) -> int:
        v = getattr(settings, name, default)
        try: return int(v)
        except: return default
    def _b(name: str, default: bool) -> bool:
        v = getattr(settings, name, default)
        if isinstance(v, bool): return v
        return str(v).strip().lower() in ("1","true","yes","on")

    return RotationParams(
        top_k=max(1, _i("rotation_top_k", 3)),
        momentum_window=max(5, _i("rotation_momentum_window", 20)),
        rebalance_every_days=max(1, _i("rotation_rebalance_every_days", 5)),
        stop_loss_pct=max(1.0, _f("rotation_stop_loss_pct", 8.0)),
        deploy_fraction=min(1.0, max(0.1, _f("rotation_deploy_fraction", 0.95))),
        regime_symbol=str(getattr(settings, "rotation_regime_symbol", "") or "000300.SS").upper(),
        regime_ma_window=max(10, _i("rotation_regime_ma_window", 60)),
        regime_enabled=_b("rotation_regime_enabled", True),
        require_uptrend=_b("rotation_require_uptrend", True),
        uptrend_ma_window=max(5, _i("rotation_uptrend_ma_window", 20)),
    )


def _closes_up_to(panel: pd.DataFrame, sym: str, as_of: pd.Timestamp) -> pd.Series:
    if (sym, "Close") not in panel.columns:
        return pd.Series(dtype=float)
    try:
        sub = panel.loc[:as_of]
    except KeyError:
        sub = panel[panel.index <= as_of]
    return sub[(sym, "Close")].dropna().astype(float)


def _is_bull_regime(panel: pd.DataFrame, as_of: pd.Timestamp, params: RotationParams) -> bool:
    if not params.regime_enabled:
        return True
    closes = _closes_up_to(panel, params.regime_symbol, as_of)
    if len(closes) < params.regime_ma_window:
        return True
    ma = float(closes.tail(params.regime_ma_window).mean())
    last = float(closes.iloc[-1])
    return last > ma > 0


def _score_etf(panel: pd.DataFrame, sym: str, as_of: pd.Timestamp, params: RotationParams) -> float | None:
    closes = _closes_up_to(panel, sym, as_of)
    if len(closes) < params.momentum_window + 1:
        return None
    last = float(closes.iloc[-1])
    if last <= 0:
        return None
    # 趋势过滤
    if params.require_uptrend and len(closes) >= params.uptrend_ma_window:
        ma = float(closes.tail(params.uptrend_ma_window).mean())
        if last < ma:
            return None
    # 动量评分（绝对收益率）
    base = float(closes.iloc[-(params.momentum_window + 1)])
    if base <= 0:
        return None
    return (last / base - 1.0) * 100.0


def _lot_floor(shares: int, lot_size: int) -> int:
    return (shares // lot_size) * lot_size if lot_size > 1 else shares


def decide(
    *,
    panel: pd.DataFrame,
    as_of: pd.Timestamp,
    positions: dict[str, float],
    avg_cost: dict[str, float],
    cash: float,
    equity: float,
    lot_size: int,
    fee_rate: float,
    params: RotationParams,
) -> tuple[list[dict[str, Any]], list[str]]:
    """生成 actions + 候选 ETF 列表（供 engine ensure panel）。"""
    actions: list[dict[str, Any]] = []

    # ---- 0. 大盘择时：HS300 < MA60 → 清仓 ----
    if not _is_bull_regime(panel, as_of, params):
        for sym, shares in positions.items():
            if shares > 0:
                actions.append({"symbol": sym, "side": "sell",
                                "shares": int(shares), "reason": "大盘空仓"})
        return actions, ETF_UNIVERSE

    # ---- 1. 评分 + 排序 ----
    scored: list[tuple[str, float]] = []
    for sym in ETF_UNIVERSE:
        s = _score_etf(panel, sym, as_of, params)
        if s is None:
            continue
        scored.append((sym, s))
    scored.sort(key=lambda x: -x[1])

    if not scored:
        return actions, ETF_UNIVERSE

    targets = [sym for sym, _ in scored[: params.top_k]]
    target_set = set(targets)

    # ---- 2. 止损 ----
    for sym, shares in positions.items():
        if shares <= 0:
            continue
        closes = _closes_up_to(panel, sym, as_of)
        if closes.empty:
            continue
        last = float(closes.iloc[-1])
        ac = avg_cost.get(sym, 0.0)
        if ac > 0:
            pnl_pct = (last / ac - 1.0) * 100.0
            if pnl_pct <= -params.stop_loss_pct:
                actions.append({"symbol": sym, "side": "sell",
                                "shares": int(shares),
                                "reason": f"止损{pnl_pct:.1f}%"})
                continue

    # ---- 3. 轮出：持仓 ETF 不在新 top_k 中 → 卖出 ----
    already_sold = {a["symbol"] for a in actions}
    for sym, shares in positions.items():
        if shares <= 0 or sym in already_sold:
            continue
        if sym not in target_set:
            actions.append({"symbol": sym, "side": "sell",
                            "shares": int(shares), "reason": "轮出"})

    # ---- 4. 买入新目标（等权）----
    prices_now: dict[str, float] = {}
    for sym in targets:
        c = _closes_up_to(panel, sym, as_of)
        if not c.empty:
            prices_now[sym] = float(c.iloc[-1])

    # 估算预算
    sell_proceeds = 0.0
    held_now = dict(positions)
    for a in actions:
        if a["side"] == "sell":
            sym = a["symbol"]
            c = _closes_up_to(panel, sym, as_of)
            if not c.empty:
                sell_proceeds += float(c.iloc[-1]) * int(a["shares"]) * (1 - fee_rate)
            held_now[sym] = held_now.get(sym, 0) - a["shares"]

    # 持仓中保留的 target 市值（避免重复加仓）
    held_target_mv = 0.0
    for sym in target_set:
        if held_now.get(sym, 0) > 0 and sym in prices_now:
            held_target_mv += held_now[sym] * prices_now[sym]

    budget = (cash + sell_proceeds + held_target_mv) * params.deploy_fraction
    per_target = budget / max(1, len(targets))

    for sym in targets:
        px = prices_now.get(sym, 0.0)
        if px <= 0:
            continue
        held = float(held_now.get(sym, 0.0))
        cur_mv = held * px
        delta_mv = per_target - cur_mv
        if delta_mv <= 0:
            continue
        shares_to_buy = int(math.floor(delta_mv / (px * (1 + fee_rate))))
        shares_to_buy = _lot_floor(shares_to_buy, lot_size)
        if shares_to_buy <= 0:
            continue
        actions.append({"symbol": sym, "side": "buy",
                        "shares": shares_to_buy, "reason": "行业轮入"})

    return actions, ETF_UNIVERSE
