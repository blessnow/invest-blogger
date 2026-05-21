"""mainline 策略 — 游资派主升浪追踪。

设计思路（小白派 + 短线游资派融合）：

  选股（每日 T，仅用 T-1 收盘后数据）：
    1) 从 mainline_scanner.build_candidates 拿主线候选
    2) 过滤：连板 ≥ MIN_CONSEQ，市值在 [MIN_MC, MAX_MC] 亿，近 5 日 < MAX_RECENT_RUN
    3) 按 score（连板数 + 龙虎榜 + 主线行业加成）排序取 top_k

  买入：
    - 候选股近 N 日没破 5/10 均线（趋势完好）
    - 当日开盘价相对昨收涨幅 < 7%（不追开盘暴涨）
    - 等权配置到 top_k 只
    - max_position_fraction 由 engine 层硬约束

  卖出（任意触发即清仓该股）：
    - 当日跌幅 > MAINLINE_STOP_LOSS_PCT
    - 跌破 MAINLINE_TRAIL_MA 日均线
    - 累计盈利 > MAINLINE_TAKE_PROFIT_PCT 且当日收阴

  仓位：满仓持有 top_k 只，否则空仓；调仓周期 MAINLINE_REBALANCE_EVERY_DAYS
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from invest_system.config import Settings
from invest_system.mainline_scanner import build_candidates, build_strength_pool


@dataclass
class MainlineParams:
    top_k: int = 3
    min_conseq_limits: int = 2          # 至少 2 板才入选
    max_prior5d_ret_pct: float = 60.0   # 近 5 日已涨超过这个不追
    min_market_cap_yi: float = 20.0
    max_market_cap_yi: float = 500.0
    stop_loss_pct: float = 7.0          # 当日跌幅止损
    trail_ma_days: int = 5              # 跌破此均线卖出
    take_profit_pct: float = 30.0       # 累计盈利触发减仓阈值
    rebalance_every_days: int = 2       # 调仓周期
    deploy_fraction: float = 0.95
    max_open_gap_pct: float = 7.0       # 跳空开盘超此不追
    candidate_pool_size: int = 10       # 扫描器返回的池子大小
    # ---- 大盘择时（regime gate）----
    regime_symbol: str = "000300.SS"    # 沪深300
    regime_ma_window: int = 20          # 跌破 MA20 全空仓
    regime_enabled: bool = True         # 是否启用
    # ---- 回调买入 (pullback entry) ----
    pullback_lookback_days: int = 10    # 强势股识别窗口（曾经 N 日内有过 conseq 板）
    pullback_from_high_min: float = 5.0  # 距近期高点回撤至少 X%（避免追高）
    pullback_from_high_max: float = 20.0  # 距近期高点回撤不超过 X%（避免暴跌票）
    pullback_recent_chg_min: float = -3.0  # 当日跌幅不超过 X%（企稳）
    pullback_recent_chg_max: float = 5.0   # 当日涨幅不超过 X%（不追涨）
    pullback_above_ma: int = 10         # 当日收盘必须 > MA10（趋势完好）
    pullback_min_days_since_limit: int = 1  # 距上次涨停至少 X 天（不在涨停日追入）


def params_from_settings(settings: Settings) -> MainlineParams:
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

    def _b(name: str, default: bool) -> bool:
        v = getattr(settings, name, default)
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    return MainlineParams(
        top_k=max(1, _i("mainline_top_k", 3)),
        min_conseq_limits=max(1, _i("mainline_min_conseq_limits", 2)),
        max_prior5d_ret_pct=_f("mainline_max_prior5d_ret_pct", 60.0),
        min_market_cap_yi=_f("mainline_min_market_cap_yi", 20.0),
        max_market_cap_yi=_f("mainline_max_market_cap_yi", 500.0),
        stop_loss_pct=max(1.0, _f("mainline_stop_loss_pct", 7.0)),
        trail_ma_days=max(2, _i("mainline_trail_ma_days", 5)),
        take_profit_pct=_f("mainline_take_profit_pct", 30.0),
        rebalance_every_days=max(1, _i("mainline_rebalance_every_days", 2)),
        deploy_fraction=min(1.0, max(0.1, _f("mainline_deploy_fraction", 0.95))),
        max_open_gap_pct=_f("mainline_max_open_gap_pct", 7.0),
        candidate_pool_size=max(3, _i("mainline_candidate_pool_size", 10)),
        regime_symbol=str(getattr(settings, "mainline_regime_symbol", "") or "000300.SS").strip().upper(),
        regime_ma_window=max(5, _i("mainline_regime_ma_window", 20)),
        regime_enabled=_b("mainline_regime_enabled", True),
        pullback_lookback_days=max(3, _i("mainline_pullback_lookback_days", 10)),
        pullback_from_high_min=_f("mainline_pullback_from_high_min", 5.0),
        pullback_from_high_max=_f("mainline_pullback_from_high_max", 20.0),
        pullback_recent_chg_min=_f("mainline_pullback_recent_chg_min", -3.0),
        pullback_recent_chg_max=_f("mainline_pullback_recent_chg_max", 5.0),
        pullback_above_ma=max(2, _i("mainline_pullback_above_ma", 10)),
        pullback_min_days_since_limit=max(0, _i("mainline_pullback_min_days_since_limit", 1)),
    )


def _closes_up_to(panel: pd.DataFrame, sym: str, as_of: pd.Timestamp) -> pd.Series:
    if (sym, "Close") not in panel.columns:
        return pd.Series(dtype=float)
    try:
        sub = panel.loc[:as_of]
    except KeyError:
        sub = panel[panel.index <= as_of]
    return sub[(sym, "Close")].dropna().astype(float)


def _today_change_pct(panel: pd.DataFrame, sym: str, as_of: pd.Timestamp) -> float | None:
    """T 日相对 T-1 收盘的涨跌幅 %。"""
    closes = _closes_up_to(panel, sym, as_of)
    if len(closes) < 2:
        return None
    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2])
    if prev <= 0:
        return None
    return (last / prev - 1.0) * 100.0


def _is_bull_regime(panel: pd.DataFrame, as_of: pd.Timestamp, params: MainlineParams) -> bool:
    """大盘择时：HS300 收盘价 > MA20 → 多头。"""
    if not params.regime_enabled:
        return True
    closes = _closes_up_to(panel, params.regime_symbol, as_of)
    if len(closes) < params.regime_ma_window:
        return True  # 数据不足保守视为多头
    ma = float(closes.tail(params.regime_ma_window).mean())
    last = float(closes.iloc[-1])
    return last > ma > 0


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
    decision_day: date,
    data_dir: Path,
    positions: dict[str, float],
    avg_cost: dict[str, float],
    cash: float,
    equity: float,
    lot_size: int,
    fee_rate: float,
    params: MainlineParams,
) -> tuple[list[dict[str, Any]], list[str]]:
    """生成 actions 和当日选中的 candidate symbols（用于日志）。"""
    actions: list[dict[str, Any]] = []

    # ---- 0. 大盘择时：HS300 跌破 MA20 全空仓 ----
    bull = _is_bull_regime(panel, as_of, params)
    if not bull:
        for sym, shares in positions.items():
            if shares > 0:
                actions.append({
                    "symbol": sym, "side": "sell",
                    "shares": int(shares),
                    "reason": "大盘空仓",
                })
        return actions, []

    # ---- 1. 卖出逻辑 ----
    for sym, shares in list(positions.items()):
        if shares <= 0:
            continue
        closes = _closes_up_to(panel, sym, as_of)
        if len(closes) < 2:
            continue
        last = float(closes.iloc[-1])
        chg = _today_change_pct(panel, sym, as_of)
        ac = avg_cost.get(sym, 0.0)
        pnl_pct = (last / ac - 1.0) * 100.0 if ac > 0 else 0.0

        reason = None
        # 当日重挫
        if chg is not None and chg <= -params.stop_loss_pct:
            reason = f"日跌{chg:.1f}%止损"
        # 跌破均线
        elif len(closes) >= params.trail_ma_days:
            ma = float(closes.tail(params.trail_ma_days).mean())
            if last < ma:
                reason = f"破MA{params.trail_ma_days}"
        # 高位收阴止盈
        if reason is None and pnl_pct >= params.take_profit_pct:
            if chg is not None and chg < 0:
                reason = f"盈{pnl_pct:.0f}%收阴止盈"

        if reason:
            actions.append({
                "symbol": sym, "side": "sell",
                "shares": int(shares),
                "reason": reason,
            })

    # ---- 2. 选股 ----
    held_set = {sym for sym, sh in positions.items() if sh > 0}
    # 改用强势股池（近 N 日有过 conseq 板的股票，不要求昨天涨停）
    cands = build_strength_pool(
        decision_day=decision_day, data_dir=data_dir,
        lookback_days=params.pullback_lookback_days,
        min_conseq_limits=params.min_conseq_limits,
        min_market_cap_yi=params.min_market_cap_yi,
        max_market_cap_yi=params.max_market_cap_yi,
        held_symbols=held_set,
    )
    cand_syms_all = [c.symbol for c in cands[: params.candidate_pool_size * 2]]

    # 回调买入过滤：从近 N 日高点回撤合理 + 当日企稳 + 站稳均线 + 远离涨停日
    tradable: list[tuple[str, float, MainlineCandidate]] = []
    for c in cands:
        closes = _closes_up_to(panel, c.symbol, as_of)
        if len(closes) < params.pullback_above_ma + 1:
            continue
        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) >= 2 else last
        if last <= 0 or prev <= 0:
            continue

        # 1. 当日涨跌幅
        chg_pct = (last / prev - 1.0) * 100.0
        if chg_pct < params.pullback_recent_chg_min:
            continue  # 当日跌太多（非企稳）
        if chg_pct > params.pullback_recent_chg_max:
            continue  # 当日涨太多（追高）

        # 2. 距近期高点回撤幅度
        lookback = closes.tail(params.pullback_lookback_days + 1)
        recent_high = float(lookback.max())
        if recent_high <= 0:
            continue
        drawdown_from_high = (1.0 - last / recent_high) * 100.0
        # 持仓股不再检查回撤（已在仓内，不能要求它再回调一次）
        if c.symbol not in held_set:
            if drawdown_from_high < params.pullback_from_high_min:
                continue  # 还在高点附近，不追
            if drawdown_from_high > params.pullback_from_high_max:
                continue  # 已暴跌，趋势可能破坏

        # 3. 站稳均线（趋势完好）
        if len(closes) >= params.pullback_above_ma:
            ma = float(closes.tail(params.pullback_above_ma).mean())
            if last < ma:
                continue

        # 4. 距上次涨停 ≥ N 天（不在涨停日追，c.prior_5d_ret_pct 复用为距今天数）
        days_since_limit = int(c.prior_5d_ret_pct)
        if days_since_limit < params.pullback_min_days_since_limit:
            continue

        # 5. 缩量过滤：当日成交量必须低于近 5 日均量 × 1.3（健康回调是缩量，放量是出货）
        if (c.symbol, "Volume") in panel.columns:
            try:
                vols = panel.loc[:as_of, (c.symbol, "Volume")].dropna().astype(float)
                if len(vols) >= 6:
                    last_vol = float(vols.iloc[-1])
                    avg5 = float(vols.tail(6).iloc[:-1].mean())  # 不含当日
                    if avg5 > 0 and last_vol > avg5 * 1.3:
                        continue  # 放量回调，疑似出货
            except Exception:
                pass

        tradable.append((c.symbol, last, c))

    # 目标集：top_k 个新候选 + 仍在 candidate_pool 中的持仓股（避免高买低卖）
    top_targets = [(s, p) for s, p, _c in tradable[: params.top_k]]
    top_set = {sym for sym, _ in top_targets}
    held_in_pool = {c.symbol for c in cands if c.symbol in positions}

    # 实际"持有目标"：合并 top_k 和"持仓且仍在候选池"
    target_set = top_set | held_in_pool

    # ---- 3. 卖出彻底退出主线（不在候选池）的持仓 ----
    already_sold = {a["symbol"] for a in actions}
    for sym, shares in positions.items():
        if shares <= 0 or sym in already_sold:
            continue
        if sym not in target_set:
            actions.append({
                "symbol": sym, "side": "sell",
                "shares": int(shares),
                "reason": "退出主线",
            })

    # 真正要买入的目标：top_k 中 - 已持有的
    targets = [(s, p) for s, p in top_targets if s not in positions or positions[s] <= 0]

    # ---- 4. 买入新目标 ----
    if not targets:
        return actions, cand_syms_all

    # 计算预算
    sell_proceeds = 0.0
    held_now: dict[str, float] = dict(positions)
    for a in actions:
        if a["side"] == "sell":
            sym = a["symbol"]
            closes = _closes_up_to(panel, sym, as_of)
            if closes.empty:
                continue
            px = float(closes.iloc[-1])
            sell_proceeds += px * int(a["shares"]) * (1.0 - fee_rate)
            held_now[sym] = held_now.get(sym, 0) - a["shares"]

    # 预算分配：算上已持有"目标集"的市值，让仓位更均衡
    held_target_mv = 0.0
    for sym in target_set:
        if sym in held_now and held_now[sym] > 0:
            cl = _closes_up_to(panel, sym, as_of)
            if not cl.empty:
                held_target_mv += held_now[sym] * float(cl.iloc[-1])

    budget_total = (cash + sell_proceeds + held_target_mv) * params.deploy_fraction
    per_target = budget_total / max(1, len(target_set))

    for sym, px in targets:
        if px <= 0:
            continue
        held = float(held_now.get(sym, 0.0))
        cur_mv = held * px
        delta_mv = per_target - cur_mv
        if delta_mv <= 0:
            continue
        shares_to_buy = int(math.floor(delta_mv / (px * (1.0 + fee_rate))))
        shares_to_buy = _lot_floor(shares_to_buy, lot_size)
        if shares_to_buy <= 0:
            continue
        actions.append({
            "symbol": sym, "side": "buy",
            "shares": shares_to_buy,
            "reason": "主线龙头",
        })

    return actions, cand_syms_all
