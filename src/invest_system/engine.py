from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from invest_system.benchmarks import format_benchmark_prices
from invest_system.config import Settings
from invest_system.data_feed import ensure_panel_has_symbols, fetch_intraday_last_prices, latest_row
from invest_system.assistant.runner import run_intraday_assistant_for_day
from invest_system.market_scanner import scan_cn_candidates_with_akshare
from invest_system.market_context import fetch_external_context
from invest_system.errors import dump_llm_raw, log_error
from invest_system.llm_strategy import (
    build_user_prompt,
    glm_decision_sync,
    system_prompt_for,
    validate_decision,
)
from invest_system.portfolio import Portfolio
from invest_system.broker import Broker, OrderResult
from invest_system.symbols import is_valid_cn_yahoo_symbol
from invest_system.engine_hooks import get_hook_registry

# ---------------------------------------------------------------------------
# Runtime-configurable parameters (modified by evolution system)
# ---------------------------------------------------------------------------
_RUNTIME_PARAMS: dict[str, float | int] = {
    "candidate_score_weight_ret1d": 0.65,
    "candidate_score_weight_ret5d": 0.35,
    "recent_bars_lookback": 15,
    "watchlist_cap": 27,
    "deploy_fraction": 0.95,
}

_CUSTOM_SCORING_FN: Any | None = None


def get_runtime_params() -> dict[str, float | int]:
    return dict(_RUNTIME_PARAMS)


def set_runtime_params(params: dict[str, float | int]) -> None:
    _RUNTIME_PARAMS.update(params)


def get_custom_scoring_fn() -> Any | None:
    return _CUSTOM_SCORING_FN


def set_custom_scoring_fn(fn: Any | None) -> None:
    global _CUSTOM_SCORING_FN
    _CUSTOM_SCORING_FN = fn


def _lot_floor(shares: int, lot_size: int) -> int:
    if shares <= 0:
        return 0
    if lot_size <= 1:
        return shares
    return (shares // lot_size) * lot_size


@dataclass
class EquitySnapshot:
    day: date
    cash: float
    equity: float
    positions: dict[str, float]


def _fmt_recent_bars(df: pd.DataFrame, symbols: list[str], as_of: pd.Timestamp, lookback: int | None = None) -> str:
    lb = lookback if lookback is not None else int(_RUNTIME_PARAMS["recent_bars_lookback"])
    try:
        sub = df.loc[:as_of].tail(lb)
    except KeyError:
        sub = df[df.index <= as_of].tail(lb)
    if isinstance(sub.columns, pd.MultiIndex):
        sub = sub.sort_index(axis=1)
    lines: list[str] = []
    for sym in symbols:
        try:
            closes = sub[(sym, "Close")].dropna()
            if closes.empty:
                continue
            last = float(closes.iloc[-1])
            lo = float(closes.min())
            hi = float(closes.max())
            lines.append(f"{sym}: close={last:.2f}, range[{lookback}d]={lo:.2f}-{hi:.2f}")
        except (KeyError, TypeError, ValueError):
            continue
    return "\n".join(lines) if lines else "(no bar data)"


def _build_candidate_pool(
    df: pd.DataFrame,
    symbols: list[str],
    as_of: pd.Timestamp,
    *,
    top_n: int,
) -> tuple[list[str], str]:
    """Rank symbols by short-term momentum + liquidity for LLM free-selection guidance."""
    if top_n <= 0 or not symbols:
        return [], ""
    unique = list(dict.fromkeys([s.strip().upper() for s in symbols if s.strip()]))
    if not unique:
        return [], ""
    try:
        sub = df.loc[:as_of].tail(15)
    except KeyError:
        sub = df[df.index <= as_of].tail(15)

    rows: list[dict[str, float | str]] = []
    for sym in unique:
        try:
            closes = sub[(sym, "Close")].dropna()
            if len(closes) < 2:
                continue
            last = float(closes.iloc[-1])
            prev = float(closes.iloc[-2])
            if prev <= 0:
                continue
            ret1 = (last / prev - 1.0) * 100.0
            if len(closes) >= 6 and float(closes.iloc[-6]) > 0:
                ret5 = (last / float(closes.iloc[-6]) - 1.0) * 100.0
            else:
                ret5 = ret1
            vol = sub[(sym, "Volume")].dropna() if (sym, "Volume") in sub.columns else pd.Series(dtype=float)
            last_vol = float(vol.iloc[-1]) if not vol.empty else 0.0
            notional = max(0.0, last * last_vol)
            # Compute enriched features for hooks
            ret10 = ret5
            if len(closes) >= 11 and float(closes.iloc[-11]) > 0:
                ret10 = (last / float(closes.iloc[-11]) - 1.0) * 100.0
            ret20 = ret10
            if len(closes) >= 21 and float(closes.iloc[-21]) > 0:
                ret20 = (last / float(closes.iloc[-21]) - 1.0) * 100.0
            vol_series = sub[(sym, "Volume")].dropna() if (sym, "Volume") in sub.columns else pd.Series(dtype=float)
            avg_vol_5d = float(vol_series.tail(5).mean()) if len(vol_series) >= 1 else 0.0
            recent_closes = closes.tail(10)
            volatility_10d = float(recent_closes.pct_change().dropna().std()) if len(recent_closes) >= 3 else 0.0
            high_10d = float(closes.tail(10).max()) if len(closes) >= 1 else last
            low_10d = float(closes.tail(10).min()) if len(closes) >= 1 else last

            hook_ctx = {
                "symbol": sym, "ret1d_pct": ret1, "ret5d_pct": ret5,
                "ret10d_pct": ret10, "ret20d_pct": ret20,
                "last_close": last, "avg_volume_5d": avg_vol_5d,
                "notional": notional, "volatility_10d": volatility_10d,
                "high_10d": high_10d, "low_10d": low_10d,
            }
            hooks = get_hook_registry()
            if hooks._hooks.get("score_candidate") is not None:
                score = hooks.score_candidate(hook_ctx)
            elif _CUSTOM_SCORING_FN is not None:
                try:
                    score = float(_CUSTOM_SCORING_FN({"ret1d": ret1, "ret5d": ret5, "last_close": last, "notional": notional}))
                except Exception:
                    score = 0.0
            else:
                w1 = float(_RUNTIME_PARAMS["candidate_score_weight_ret1d"])
                w5 = float(_RUNTIME_PARAMS["candidate_score_weight_ret5d"])
                score = w1 * ret1 + w5 * ret5
            rows.append(
                {
                    "symbol": sym,
                    "score": score,
                    "ret1d_pct": ret1,
                    "ret5d_pct": ret5,
                    "last_close": last,
                    "notional": notional,
                }
            )
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    if not rows:
        return [], ""

    rows = sorted(rows, key=lambda x: (float(x["score"]), float(x["notional"])), reverse=True)[:top_n]
    picked = [str(r["symbol"]) for r in rows]
    lines = [f"candidates={picked}"]
    for r in rows[: min(15, len(rows))]:
        lines.append(
            f"{r['symbol']}: score={float(r['score']):.2f}, ret1d={float(r['ret1d_pct']):.2f}%, "
            f"ret5d={float(r['ret5d_pct']):.2f}%, close={float(r['last_close']):.2f}"
        )
    return picked, "\n".join(lines)


def _cap_buy_shares(
    portfolio: Portfolio,
    symbol: str,
    want: int,
    price: float,
    max_fraction: float,
    prices: dict[str, float],
    lot_size: int,
) -> int:
    if want <= 0 or price <= 0:
        return 0
    fee_rate = portfolio.fee_rate
    max_cash = portfolio.cash
    max_by_cash = int(max_cash / (price * (1 + fee_rate)))
    eq = portfolio.equity(prices)
    current_mv = portfolio.market_value(symbol, prices)
    cap_mv = max_fraction * eq if eq > 0 else 0.0
    room_mv = max(0.0, cap_mv - current_mv)
    max_by_fraction = int(room_mv / price) if price > 0 else 0
    raw = max(0, min(want, max_by_cash, max_by_fraction))
    return _lot_floor(raw, lot_size)


def _apply_actions(
    portfolio: Portfolio,
    actions: list[dict[str, Any]],
    prices: dict[str, float],
    universe: set[str],
    day: date,
    max_fraction: float,
    lot_size: int,
    free_selection: bool,
    ts: datetime | None = None,
    broker: Broker | None = None,
) -> None:
    sells = [a for a in actions if str(a.get("side", "")).lower() == "sell"]
    buys = [a for a in actions if str(a.get("side", "")).lower() == "buy"]

    for a in sells:
        sym = str(a.get("symbol", "")).upper().strip()
        if not sym:
            continue
        if not free_selection and sym not in universe:
            continue
        if free_selection and not is_valid_cn_yahoo_symbol(sym):
            continue
        price = prices.get(sym)
        if price is None:
            continue
        try:
            want = int(float(a.get("shares", 0)))
        except (TypeError, ValueError):
            continue
        sellable = portfolio.sellable_today(day, sym)
        raw_sell = min(want, int(math.floor(sellable)))
        shares = _lot_floor(raw_sell, lot_size)
        if shares > 0:
            if broker:
                # broker.execute 内部已调 portfolio.sell，不要重复调用
                broker.execute("sell", sym, float(shares), price, day=day, ts=ts)
            else:
                portfolio.sell(day, sym, float(shares), price, ts=ts)

    for a in buys:
        sym = str(a.get("symbol", "")).upper().strip()
        if not sym:
            continue
        if not free_selection and sym not in universe:
            continue
        if free_selection and not is_valid_cn_yahoo_symbol(sym):
            continue
        price = prices.get(sym)
        if price is None:
            continue
        try:
            want = int(float(a.get("shares", 0)))
        except (TypeError, ValueError):
            continue
        # Hook: size_position
        hooks = get_hook_registry()
        if hooks._hooks.get("size_position") is not None:
            size_ctx = {
                "symbol": sym, "side": "buy", "requested_shares": want,
                "price": price,
                "portfolio": {
                    "cash": portfolio.cash, "equity": portfolio.equity(prices),
                    "positions": dict(portfolio.positions),
                    "num_positions": len(portfolio.positions),
                },
                "current_shares": portfolio.positions.get(sym, 0),
                "current_market_value": portfolio.market_value(sym, prices),
                "position_fraction": portfolio.market_value(sym, prices) / max(portfolio.equity(prices), 1),
                "max_fraction": max_fraction,
                "avg_cost": portfolio.avg_cost.get(sym),
                "unrealized_pnl_pct": (price / portfolio.avg_cost[sym] - 1) * 100 if sym in portfolio.avg_cost and portfolio.avg_cost[sym] > 0 else None,
                "recent_bars": [],
                "max_fraction_config": max_fraction,
            }
            want = hooks.size_position(size_ctx)
        capped = _cap_buy_shares(portfolio, sym, want, price, max_fraction, prices, lot_size)
        if capped > 0:
            if broker:
                # broker.execute 内部已调 portfolio.buy，不要重复调用
                broker.execute("buy", sym, float(capped), price, day=day, ts=ts)
            else:
                portfolio.buy(day, sym, float(capped), price, ts=ts)


def _initial_buy_hold(
    portfolio: Portfolio,
    prices: dict[str, float],
    universe: list[str],
    day: date,
    lot_size: int,
    deploy_fraction: float | None = None,
) -> None:
    if not prices:
        return
    frac = deploy_fraction if deploy_fraction is not None else float(_RUNTIME_PARAMS["deploy_fraction"])
    budget = portfolio.cash * frac
    per = budget / max(len(universe), 1)
    for sym in universe:
        p = prices.get(sym)
        if p is None or p <= 0:
            continue
        fee_rate = portfolio.fee_rate
        raw = int(per / (p * (1 + fee_rate)))
        shares = _lot_floor(raw, lot_size)
        if shares > 0:
            portfolio.buy(day, sym, float(shares), p)


def run_simulation(
    settings: Settings,
    price_df: pd.DataFrame,
    broker: Broker | None = None,
) -> tuple[Portfolio, list[EquitySnapshot], list[Path]]:
    free = settings.is_free_selection()
    fixed_syms = settings.symbols()
    if not free and not fixed_syms:
        raise ValueError("UNIVERSE is empty (fixed 模式需要 UNIVERSE)")

    universe = set(fixed_syms) if not free else set()
    assistant_dirs: list[Path] = []
    panel = price_df
    start = settings.start_date
    end = settings.end_date
    cache_dir = settings.data_dir

    dates = sorted(panel.index.unique())
    portfolio = Portfolio(
        cash=float(settings.initial_capital),
        fee_rate=float(settings.commission_rate),
    )
    snapshots: list[EquitySnapshot] = []

    rebalance_every = max(1, settings.rebalance_every_days)
    first_allocation_done = False
    days_since_last_rebalance = rebalance_every  # force first rebalance
    recent_equity_returns: list[float] = []
    peak_equity = float(settings.initial_capital)
    hooks = get_hook_registry()

    for i, ts in enumerate(dates):
        d = ts.date() if hasattr(ts, "date") else pd.Timestamp(ts).date()
        ts_pd = pd.Timestamp(ts)

        panel = ensure_panel_has_symbols(panel, list(portfolio.positions.keys()), start, end, cache_dir)
        if not free and fixed_syms:
            panel = ensure_panel_has_symbols(panel, fixed_syms, start, end, cache_dir)
        panel = ensure_panel_has_symbols(
            panel,
            settings.reference_benchmark_symbols(),
            start,
            end,
            cache_dir,
        )

        prices = latest_row(panel, ts_pd)

        if settings.strategy_mode.strip().lower() == "buy_hold":
            if not first_allocation_done and prices:
                if not fixed_syms:
                    raise ValueError("buy_hold 需要配置 UNIVERSE 作为建仓标的")
                panel = ensure_panel_has_symbols(panel, fixed_syms, start, end, cache_dir)
                prices = latest_row(panel, ts_pd)
                _initial_buy_hold(
                    portfolio,
                    prices,
                    fixed_syms,
                    d,
                    lot_size=int(settings.lot_size),
                )
                first_allocation_done = True
        elif prices:
            # Compute loop state for hooks
            eq_pre = portfolio.equity(prices)
            current_drawdown = (eq_pre / peak_equity - 1) * 100 if peak_equity > 0 else 0.0
            market_regime = "neutral"
            if len(recent_equity_returns) >= 5:
                avg5 = sum(recent_equity_returns[-5:]) / 5
                if avg5 > 0.3:
                    market_regime = "bull"
                elif avg5 < -0.3:
                    market_regime = "bear"

            # --- Hook: check_exit (stop-loss / take-profit) ---
            if portfolio.positions:
                positions_info: dict[str, dict] = {}
                for sym_pos, shares_pos in portfolio.positions.items():
                    px = prices.get(sym_pos)
                    if px is None or px <= 0 or shares_pos <= 0:
                        continue
                    ac = portfolio.avg_cost.get(sym_pos)
                    pnl_pct = (px / ac - 1) * 100 if ac and ac > 0 else 0.0
                    sym_bars = []
                    try:
                        closes_buf = panel.loc[:ts_pd].tail(15)
                        sym_c = closes_buf[(sym_pos, "Close")].dropna()
                        sym_bars = [float(x) for x in sym_c.tail(10)] if len(sym_c) > 0 else []
                    except Exception:
                        pass
                    positions_info[sym_pos] = {
                        "shares": shares_pos, "avg_cost": ac or 0.0,
                        "current_price": px, "unrealized_pnl_pct": pnl_pct,
                        "recent_bars": sym_bars,
                    }
                exit_ctx = {
                    "day": str(d), "positions": positions_info,
                    "portfolio": {"cash": portfolio.cash, "equity": eq_pre},
                    "market_regime": market_regime,
                }
                forced_exits = hooks.check_exit(exit_ctx)
                if forced_exits:
                    exit_actions = []
                    for ex in forced_exits:
                        sym_ex = str(ex.get("symbol", "")).upper().strip()
                        if sym_ex in portfolio.positions:
                            sellable = portfolio.sellable_today(d, sym_ex)
                            shares_ex = int(math.floor(sellable))
                            shares_ex = _lot_floor(shares_ex, int(settings.lot_size))
                            if shares_ex > 0:
                                exit_actions.append({
                                    "symbol": sym_ex, "side": "sell",
                                    "shares": shares_ex,
                                    "reason": ex.get("reason", "check_exit"),
                                })
                    if exit_actions:
                        _apply_actions(
                            portfolio, exit_actions, prices, universe, d,
                            settings.max_position_fraction, int(settings.lot_size),
                            free_selection=free, broker=broker,
                        )

            # --- Hook: should_rebalance ---
            rebalance_ctx = {
                "day_index": i, "day": str(d),
                "days_since_rebalance": days_since_last_rebalance,
                "portfolio": {
                    "cash": portfolio.cash,
                    "equity": portfolio.equity(prices),
                    "positions": dict(portfolio.positions),
                    "num_positions": len(portfolio.positions),
                },
                "prices": prices,
                "recent_returns": recent_equity_returns[-10:],
                "drawdown_pct": current_drawdown,
                "default_interval": rebalance_every,
            }
            should_rebal = hooks.should_rebalance(rebalance_ctx)
            days_since_last_rebalance += 1

            strat_mode = settings.strategy_mode.strip().lower()

            if strat_mode == "momentum":
                # momentum 策略有自己的调仓节奏（MOMENTUM_REBALANCE_EVERY_DAYS）
                from invest_system.strategies import momentum as _mom

                mp = _mom.params_from_settings(settings)
                if days_since_last_rebalance >= mp.rebalance_every_days or not portfolio.positions:
                    days_since_last_rebalance = 0
                    # 候选池：UNIVERSE + 配置的扫描池；并确保 panel 已加载基准 + 候选数据
                    cand: list[str] = list(dict.fromkeys([
                        *fixed_syms,
                        *settings.market_scan_symbols(),
                    ]))
                    bms = settings.reference_benchmark_symbols()
                    syms_needed = list(dict.fromkeys([*bms, mp.regime_symbol, *cand]))
                    panel = ensure_panel_has_symbols(panel, syms_needed, start, end, cache_dir)
                    try:
                        mom_actions = _mom.decide(
                            panel=panel, as_of=ts_pd,
                            candidate_symbols=cand,
                            positions=dict(portfolio.positions),
                            avg_cost=dict(portfolio.avg_cost),
                            cash=portfolio.cash,
                            equity=portfolio.equity(prices),
                            lot_size=int(settings.lot_size),
                            fee_rate=float(settings.commission_rate),
                            params=mp,
                        )
                    except Exception as exc:
                        log_error(
                            component="engine", phase="backtest",
                            error=exc, message="momentum 策略决策失败，本轮跳过",
                            extra={"day": str(d)},
                        )
                        mom_actions = []
                    if mom_actions:
                        action_syms = [a["symbol"] for a in mom_actions]
                        panel = ensure_panel_has_symbols(panel, action_syms, start, end, cache_dir)
                        prices = latest_row(panel, ts_pd)
                        _apply_actions(
                            portfolio, mom_actions, prices, universe, d,
                            settings.max_position_fraction, int(settings.lot_size),
                            free_selection=True, broker=broker,
                        )

            if strat_mode == "rotation":
                from invest_system.strategies import rotation as _rot

                rp = _rot.params_from_settings(settings)
                if days_since_last_rebalance >= rp.rebalance_every_days or not portfolio.positions:
                    days_since_last_rebalance = 0
                    # 确保 ETF + 基准在 panel 中
                    needed = list(dict.fromkeys([rp.regime_symbol, *_rot.ETF_UNIVERSE]))
                    panel = ensure_panel_has_symbols(panel, needed, start, end, cache_dir)
                    try:
                        rot_actions, rot_cands = _rot.decide(
                            panel=panel, as_of=ts_pd,
                            positions=dict(portfolio.positions),
                            avg_cost=dict(portfolio.avg_cost),
                            cash=portfolio.cash,
                            equity=portfolio.equity(prices),
                            lot_size=int(settings.lot_size),
                            fee_rate=float(settings.commission_rate),
                            params=rp,
                        )
                    except Exception as exc:
                        log_error(component="engine", phase="backtest",
                                  error=exc, message="rotation 策略决策失败",
                                  extra={"day": str(d)})
                        rot_actions = []
                    if rot_actions:
                        prices = latest_row(panel, ts_pd)
                        _apply_actions(
                            portfolio, rot_actions, prices, universe, d,
                            settings.max_position_fraction, int(settings.lot_size),
                            free_selection=True, broker=broker,
                        )

            if strat_mode == "mainline":
                from invest_system.strategies import mainline as _ml

                mlp = _ml.params_from_settings(settings)
                if days_since_last_rebalance >= mlp.rebalance_every_days or not portfolio.positions:
                    days_since_last_rebalance = 0
                    try:
                        ml_actions, ml_cands = _ml.decide(
                            panel=panel, as_of=ts_pd, decision_day=d,
                            data_dir=Path(settings.data_dir),
                            positions=dict(portfolio.positions),
                            avg_cost=dict(portfolio.avg_cost),
                            cash=portfolio.cash,
                            equity=portfolio.equity(prices),
                            lot_size=int(settings.lot_size),
                            fee_rate=float(settings.commission_rate),
                            params=mlp,
                        )
                    except Exception as exc:
                        log_error(
                            component="engine", phase="backtest",
                            error=exc, message="mainline 策略决策失败，本轮跳过",
                            extra={"day": str(d)},
                        )
                        ml_actions, ml_cands = [], []
                    if ml_cands:
                        # 把候选股票的行情拉进 panel，方便后续撮合
                        panel = ensure_panel_has_symbols(panel, ml_cands, start, end, cache_dir)
                        prices = latest_row(panel, ts_pd)
                    if ml_actions:
                        action_syms = [a["symbol"] for a in ml_actions]
                        panel = ensure_panel_has_symbols(panel, action_syms, start, end, cache_dir)
                        prices = latest_row(panel, ts_pd)
                        _apply_actions(
                            portfolio, ml_actions, prices, universe, d,
                            settings.max_position_fraction, int(settings.lot_size),
                            free_selection=True, broker=broker,
                        )

            if should_rebal and strat_mode == "llm":
                days_since_last_rebalance = 0
                cal = settings.calendar_symbol.strip().upper()
                bms = settings.reference_benchmark_symbols()
                others = list(
                    dict.fromkeys([*portfolio.positions.keys(), *fixed_syms, cal])
                )
                others = [w for w in others if w and w not in set(bms)]
                watch_trimmed = bms + others[: max(0, int(_RUNTIME_PARAMS["watchlist_cap"]) - len(bms))]
                candidate_symbols: list[str] = []
                candidate_text = ""
                auto_quotes: dict[str, float] = {}

                panel = ensure_panel_has_symbols(panel, watch_trimmed, start, end, cache_dir)
                if free:
                    scan_syms = settings.market_scan_symbols()
                    if scan_syms:
                        panel = ensure_panel_has_symbols(panel, scan_syms, start, end, cache_dir)
                        candidate_symbols, candidate_text = _build_candidate_pool(
                            panel,
                            scan_syms,
                            ts_pd,
                            top_n=max(5, int(settings.market_candidates_top_n)),
                        )
                        if candidate_text:
                            strict_mode = bool(settings.free_selection_enforce_candidates)
                            candidate_text = (
                                f"strict_mode={'true' if strict_mode else 'false'}"
                                "（strict=true 时仅允许候选池买入；卖出现有持仓不受限）\n"
                                + candidate_text
                            )
                    else:
                        candidate_symbols, candidate_text, auto_quotes = scan_cn_candidates_with_akshare(
                            max(5, int(settings.market_candidates_top_n)),
                            retries=max(1, int(settings.market_scan_retries)),
                            cache_file=settings.data_dir / "market_scan_cache_cn.json",
                            cache_max_age_min=max(1, int(settings.market_scan_cache_max_age_min)),
                            http_proxy=settings.market_scan_http_proxy,
                            https_proxy=settings.market_scan_https_proxy,
                            no_proxy=settings.market_scan_no_proxy,
                            probe_url=settings.market_scan_probe_url,
                            probe_timeout_sec=float(settings.market_scan_probe_timeout_sec),
                        )
                        if not candidate_symbols:
                            fallback_scan = [
                                s
                                for s in list(dict.fromkeys([*watch_trimmed, *list(portfolio.positions.keys())]))
                                if s not in set(bms)
                            ]
                            candidate_symbols, fallback_text = _build_candidate_pool(
                                panel,
                                fallback_scan,
                                ts_pd,
                                top_n=max(5, int(settings.market_candidates_top_n)),
                            )
                            if candidate_symbols and fallback_text:
                                strict_mode = bool(settings.free_selection_enforce_candidates)
                                candidate_text = (
                                    f"strict_mode={'true' if strict_mode else 'false'}"
                                    "（strict=true 时仅允许候选池买入；卖出现有持仓不受限）\n"
                                    "source=fallback_local_panel（自动扫描网络失败，使用本地行情池排序）\n"
                                    + fallback_text
                                )
                prices = latest_row(panel, ts_pd)
                recent = _fmt_recent_bars(panel, watch_trimmed, ts_pd)
                bench_text = format_benchmark_prices(prices, bms)
                equity_mtm = portfolio.equity(prices)
                intraday_quotes_text = ""
                if settings.intraday_quote_enabled:
                    probe_syms = list(
                        dict.fromkeys(
                            [
                                *candidate_symbols[: min(20, len(candidate_symbols))],
                                *watch_trimmed,
                                *list(portfolio.positions.keys()),
                            ]
                        )
                    )
                    intraday_quotes = fetch_intraday_last_prices(
                        probe_syms,
                        period=settings.intraday_quote_period,
                        interval=settings.intraday_quote_interval,
                    )
                    if free and not settings.market_scan_symbols() and auto_quotes:
                        intraday_quotes.update(auto_quotes)
                    if intraday_quotes:
                        intraday_quotes_text = json.dumps(intraday_quotes, ensure_ascii=False)
                ext_ctx = fetch_external_context(
                    settings,
                    decision_day=str(d),
                    symbols=watch_trimmed,
                    positions=dict(portfolio.positions),
                    cash=portfolio.cash,
                    equity=equity_mtm,
                    benchmarks=bms,
                )

                intraday_bundle = ""
                if settings.intraday_assistant_enabled():
                    assistant_watchlist = watch_trimmed
                    if free and candidate_symbols:
                        assistant_watchlist = list(
                            dict.fromkeys([*candidate_symbols[:15], *watch_trimmed])
                        )
                        panel = ensure_panel_has_symbols(
                            panel,
                            assistant_watchlist,
                            start,
                            end,
                            cache_dir,
                        )
                    intraday_bundle, asst_dir = run_intraday_assistant_for_day(
                        settings,
                        panel=panel,
                        ts=ts_pd,
                        decision_day=d,
                        watchlist=assistant_watchlist,
                        portfolio=portfolio,
                        prices=prices,
                        benchmarks=bms,
                    )
                    assistant_dirs.append(asst_dir)

                user = build_user_prompt(
                    watchlist=watch_trimmed,
                    free_selection=free,
                    day=str(d),
                    cash=portfolio.cash,
                    equity=equity_mtm,
                    positions=dict(portfolio.positions),
                    prices=prices,
                    initial_capital=float(settings.initial_capital),
                    benchmark_prices_text=bench_text,
                    recent_bars=recent,
                    candidate_pool_text=candidate_text,
                    intraday_quotes_text=intraday_quotes_text,
                    external_context_text=ext_ctx,
                    intraday_assistant_text=intraday_bundle,
                    lot_size=int(settings.lot_size),
                    max_position_fraction=float(settings.max_position_fraction),
                    commission_rate=float(settings.commission_rate),
                    rebalance_every_days=int(settings.rebalance_every_days),
                )
                try:
                    decision = glm_decision_sync(
                        settings,
                        system_prompt=system_prompt_for(settings.selection_mode),
                        user_prompt=user,
                        phase="backtest",
                        day=str(d),
                    )
                    actions = validate_decision(decision)
                    if actions:
                        executable_actions = actions
                        if (
                            free
                            and bool(settings.free_selection_enforce_candidates)
                        ):
                            cand_set = set(candidate_symbols)
                            hold_set = set(portfolio.positions.keys())
                            executable_actions = []
                            for a in actions:
                                sym = str(a.get("symbol", "")).upper().strip()
                                if not sym:
                                    continue
                                side = str(a.get("side", "")).lower().strip()
                                if side == "sell":
                                    if sym in hold_set:
                                        executable_actions.append(a)
                                    continue
                                if side == "buy" and sym in cand_set:
                                    executable_actions.append(a)
                        action_syms: list[str] = []
                        for a in executable_actions:
                            s = str(a.get("symbol", "")).upper().strip()
                            if s:
                                action_syms.append(s)
                        panel = ensure_panel_has_symbols(panel, action_syms, start, end, cache_dir)
                        prices = latest_row(panel, ts_pd)
                        # --- Hook: filter_risk ---
                        risk_ctx = {
                            "day": str(d), "actions": executable_actions,
                            "portfolio": {
                                "cash": portfolio.cash,
                                "equity": portfolio.equity(prices),
                                "positions": dict(portfolio.positions),
                                "num_positions": len(portfolio.positions),
                            },
                            "prices": prices,
                            "max_position_fraction": float(settings.max_position_fraction),
                            "position_fractions": {
                                sym_rf: portfolio.market_value(sym_rf, prices) / max(portfolio.equity(prices), 1)
                                for sym_rf in portfolio.positions
                            },
                            "drawdown_pct": current_drawdown,
                            "recent_returns": recent_equity_returns[-10:],
                        }
                        executable_actions = hooks.filter_risk(risk_ctx)
                        if not isinstance(executable_actions, list):
                            executable_actions = []
                        _apply_actions(
                            portfolio,
                            executable_actions,
                            prices,
                            universe,
                            d,
                            settings.max_position_fraction,
                            int(settings.lot_size),
                            free_selection=free,
                            broker=broker,
                        )
                except Exception as exc:
                    # 网络/解析失败则本轮不调仓，避免中断整段回测；落盘错误供事后回查
                    log_error(
                        component="engine", phase="backtest",
                        error=exc,
                        message=f"LLM 决策失败，本轮跳过调仓",
                        extra={"day": str(d), "num_holdings": len(portfolio.positions)},
                    )

        eq = portfolio.equity(prices)
        if eq > peak_equity:
            peak_equity = eq
        if snapshots:
            prev_eq = snapshots[-1].equity
            if prev_eq > 0:
                recent_equity_returns.append((eq / prev_eq - 1) * 100)
                if len(recent_equity_returns) > 20:
                    recent_equity_returns = recent_equity_returns[-20:]
        snapshots.append(
            EquitySnapshot(
                day=d,
                cash=portfolio.cash,
                equity=eq,
                positions=dict(portfolio.positions),
            )
        )

    return portfolio, snapshots, assistant_dirs
