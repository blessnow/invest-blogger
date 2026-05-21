"""动量轮动策略 (momentum) — A 股经典低换手低成本方案。

核心逻辑（可全部用 .env 微调）：
  1. 大盘择时：沪深300 收盘价 ≤ MA200 → 全空仓（只允许卖出）
  2. 选股池：UNIVERSE 或 MARKET_SCAN_UNIVERSE
  3. 入选条件（同时满足）：
       - close > MA20 > MA60      (多头排列)
       - 近 20 日收益率 > MOMENTUM_RET20_MIN  (近端强度)
  4. 排序：按近 60 日动量倒序，取 MOMENTUM_TOP_K 只
  5. 个股止损：浮亏 ≤ -MOMENTUM_STOP_LOSS_PCT 强制卖出
  6. 等权配置；每 MOMENTUM_REBALANCE_EVERY_DAYS 个交易日调仓
  7. 仅做多，T+1 由引擎层强制

返回 list[dict]，结构同 LLM actions，可直接送给 _apply_actions 执行。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd

from invest_system.config import Settings


@dataclass
class MomentumParams:
    top_k: int = 5
    ret20_min: float = 5.0          # 近 20 日动量阈值 (%)
    stop_loss_pct: float = 8.0      # 个股止损 (浮亏 %)
    rebalance_every_days: int = 5   # 调仓周期（交易日）
    regime_symbol: str = "000300.SS"
    regime_ma_window: int = 200
    ma_short: int = 20
    ma_long: int = 60
    momentum_window: int = 60       # 排序窗口
    deploy_fraction: float = 0.95   # 资金部署比例（留 5% 应对滑点/费用）


def params_from_settings(settings: Settings) -> MomentumParams:
    """从 .env 读取动量策略参数（容错：缺省值与边界保护）。"""

    def _f(name: str, default: float) -> float:
        v = getattr(settings, name, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _i(name: str, default: int) -> int:
        v = getattr(settings, name, default)
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    return MomentumParams(
        top_k=max(1, _i("momentum_top_k", 5)),
        ret20_min=_f("momentum_ret20_min", 5.0),
        stop_loss_pct=max(0.1, _f("momentum_stop_loss_pct", 8.0)),
        rebalance_every_days=max(1, _i("momentum_rebalance_every_days", 5)),
        regime_symbol=str(getattr(settings, "momentum_regime_symbol", "") or "000300.SS").strip().upper(),
        regime_ma_window=max(20, _i("momentum_regime_ma_window", 200)),
        ma_short=max(2, _i("momentum_ma_short", 20)),
        ma_long=max(5, _i("momentum_ma_long", 60)),
        momentum_window=max(5, _i("momentum_window", 60)),
        deploy_fraction=min(1.0, max(0.1, _f("momentum_deploy_fraction", 0.95))),
    )


def _closes_up_to(panel: pd.DataFrame, sym: str, as_of: pd.Timestamp) -> pd.Series:
    if (sym, "Close") not in panel.columns:
        return pd.Series(dtype=float)
    try:
        sub = panel.loc[:as_of]
    except KeyError:
        sub = panel[panel.index <= as_of]
    s = sub[(sym, "Close")].dropna()
    return s.astype(float)


def _is_bull_regime(panel: pd.DataFrame, as_of: pd.Timestamp, params: MomentumParams) -> bool:
    """大盘择时：基准 close > MA(window) → 多头。"""
    closes = _closes_up_to(panel, params.regime_symbol, as_of)
    if len(closes) < params.regime_ma_window:
        # 历史不足时保守地视为多头（避免冷启动空仓不动）
        return True
    ma = float(closes.tail(params.regime_ma_window).mean())
    last = float(closes.iloc[-1])
    return last > ma > 0


def _score_symbol(
    panel: pd.DataFrame,
    sym: str,
    as_of: pd.Timestamp,
    params: MomentumParams,
) -> dict[str, float] | None:
    """对单只股票打分；不符合多头排列或近端动量阈值则返回 None。"""
    closes = _closes_up_to(panel, sym, as_of)
    needed = max(params.ma_long, params.momentum_window) + 1
    if len(closes) < needed:
        return None
    last = float(closes.iloc[-1])
    if last <= 0:
        return None
    ma_s = float(closes.tail(params.ma_short).mean())
    ma_l = float(closes.tail(params.ma_long).mean())
    if not (last > ma_s > ma_l > 0):
        return None
    # 近 20 日动量
    base_20 = float(closes.iloc[-21]) if len(closes) >= 21 else float(closes.iloc[0])
    if base_20 <= 0:
        return None
    ret20 = (last / base_20 - 1.0) * 100.0
    if ret20 < params.ret20_min:
        return None
    # 排序用 N 日动量
    base_w = float(closes.iloc[-(params.momentum_window + 1)]) if len(closes) > params.momentum_window else float(closes.iloc[0])
    if base_w <= 0:
        return None
    score = (last / base_w - 1.0) * 100.0
    return {"score": score, "ret20": ret20, "last": last}


def select_targets(
    panel: pd.DataFrame,
    candidate_symbols: list[str],
    as_of: pd.Timestamp,
    params: MomentumParams,
) -> list[tuple[str, float]]:
    """返回 [(symbol, last_price)] top-K；不满足条件返回空。"""
    if not _is_bull_regime(panel, as_of, params):
        return []
    scored: list[tuple[str, float, float]] = []
    seen: set[str] = set()
    for sym in candidate_symbols:
        sym = sym.strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        info = _score_symbol(panel, sym, as_of, params)
        if info is None:
            continue
        scored.append((sym, info["score"], info["last"]))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [(s, px) for s, _sc, px in scored[: params.top_k]]


def _lot_floor(shares: int, lot_size: int) -> int:
    if shares <= 0:
        return 0
    if lot_size <= 1:
        return shares
    return (shares // lot_size) * lot_size


def decide(
    *,
    panel: pd.DataFrame,
    as_of: pd.Timestamp,
    candidate_symbols: list[str],
    positions: dict[str, float],
    avg_cost: dict[str, float],
    cash: float,
    equity: float,
    lot_size: int,
    fee_rate: float,
    params: MomentumParams,
) -> list[dict[str, Any]]:
    """根据当前状态生成 actions list（同 LLM 输出格式）。"""
    actions: list[dict[str, Any]] = []
    prices_now: dict[str, float] = {}
    for sym in list(positions.keys()):
        c = _closes_up_to(panel, sym, as_of)
        if not c.empty:
            prices_now[sym] = float(c.iloc[-1])

    # --- Step 1: 个股止损（始终执行，无论 regime）---
    for sym, shares in positions.items():
        if shares <= 0:
            continue
        ac = avg_cost.get(sym, 0.0)
        px = prices_now.get(sym, 0.0)
        if ac <= 0 or px <= 0:
            continue
        pnl_pct = (px / ac - 1.0) * 100.0
        if pnl_pct <= -params.stop_loss_pct:
            actions.append({
                "symbol": sym, "side": "sell",
                "shares": int(shares),
                "reason": f"止损 {pnl_pct:.1f}%",
            })

    # --- Step 2: regime 判定 ---
    bull = _is_bull_regime(panel, as_of, params)
    if not bull:
        # 熊市：清空所有未止损持仓
        stopped = {a["symbol"] for a in actions}
        for sym, shares in positions.items():
            if sym in stopped or shares <= 0:
                continue
            actions.append({
                "symbol": sym, "side": "sell",
                "shares": int(shares),
                "reason": "大盘择时-空仓",
            })
        return actions

    # --- Step 3: 选目标 ---
    targets = select_targets(panel, candidate_symbols, as_of, params)
    target_set = {sym for sym, _ in targets}

    # --- Step 4: 卖出不在目标集中的持仓（除已止损）---
    stopped = {a["symbol"] for a in actions}
    for sym, shares in positions.items():
        if sym in stopped or shares <= 0:
            continue
        if sym not in target_set:
            actions.append({
                "symbol": sym, "side": "sell",
                "shares": int(shares),
                "reason": "轮出目标池",
            })

    # --- Step 5: 买入目标（等权）---
    if not targets:
        return actions

    # 估算调仓后可用资金：当前现金 + 即将卖出的市值（保守按 (1 - fee_rate) 折扣）
    sell_proceeds = 0.0
    for a in actions:
        if a["side"] == "sell":
            px = prices_now.get(a["symbol"], 0.0)
            sell_proceeds += px * int(a["shares"]) * (1.0 - fee_rate)
    budget_total = (cash + sell_proceeds) * params.deploy_fraction
    per_target = budget_total / max(1, len(targets))

    # 已持有的目标按差额加仓（这里简化：只补到等权目标）
    for sym, px in targets:
        if px <= 0:
            continue
        held = float(positions.get(sym, 0.0))
        cur_mv = held * px
        want_mv = per_target
        delta_mv = want_mv - cur_mv
        if delta_mv <= 0:
            continue  # 已超配则不动；卖出由 Step 4 处理
        # 留出佣金缓冲
        shares_to_buy = int(math.floor(delta_mv / (px * (1.0 + fee_rate))))
        shares_to_buy = _lot_floor(shares_to_buy, lot_size)
        if shares_to_buy <= 0:
            continue
        actions.append({
            "symbol": sym, "side": "buy",
            "shares": shares_to_buy,
            "reason": "动量买入",
        })

    return actions
