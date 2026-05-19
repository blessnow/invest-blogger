"""盘中定时节点：拉实盘参考价 → 看盘助手(单节点) → DeepSeek 调仓 → 持久化模拟持仓。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from invest_system.assistant.constants import INTRADAY_PHASES
from invest_system.assistant.runner import run_single_intraday_phase
from invest_system.benchmarks import format_benchmark_prices
from invest_system.config import Settings, load_settings
from invest_system.data_feed import (
    build_live_execution_prices,
    download_prices,
    ensure_panel_has_symbols,
    latest_row,
)
from invest_system.engine import _apply_actions, _build_candidate_pool, _fmt_recent_bars
from invest_system.llm_strategy import (
    build_user_prompt,
    glm_decision_sync,
    system_prompt_for,
)
from invest_system.market_context import fetch_external_context
from invest_system.market_scanner import scan_cn_candidates_with_akshare
from invest_system.portfolio import Portfolio, Transaction
from invest_system.transactions_io import write_transactions_csv

PHASE_BY_KEY: dict[str, str] = dict(INTRADAY_PHASES)


def _shanghai_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def load_live_portfolio(
    path: Path,
    *,
    initial_capital: float,
    fee_rate: float,
    t_plus_1_enabled: bool = True,
) -> Portfolio:
    if not path.is_file():
        return Portfolio(
            cash=float(initial_capital),
            fee_rate=float(fee_rate),
            t_plus_1_enabled=bool(t_plus_1_enabled),
        )
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return Portfolio(
            cash=float(initial_capital),
            fee_rate=float(fee_rate),
            t_plus_1_enabled=bool(t_plus_1_enabled),
        )
    cash = float(blob.get("cash", initial_capital))
    fee = float(blob.get("fee_rate", fee_rate))
    positions = {str(k).upper(): float(v) for k, v in (blob.get("positions") or {}).items()}
    avg_cost = {
        str(k).upper(): float(v)
        for k, v in (blob.get("avg_cost") or {}).items()
        if str(v).strip()
    }
    txs: list[Transaction] = []
    sh_tz = ZoneInfo("Asia/Shanghai")
    for row in blob.get("transactions") or []:
        if not isinstance(row, dict):
            continue
        ac_before = row.get("avg_cost_before")
        rp = row.get("realized_pnl")
        ts_str = row.get("timestamp")
        ts_str = str(ts_str).strip() if ts_str not in (None, "") else None
        day_obj = date.fromisoformat(str(row["day"]))
        if ts_str is None:
            # 旧记录回填：当天 15:00 上海时间，保证排序稳定且下次 save 自动持久化
            fb = datetime.combine(day_obj, time(15, 0, 0), tzinfo=sh_tz)
            ts_str = fb.isoformat(timespec="seconds")
        txs.append(
            Transaction(
                day=day_obj,
                symbol=str(row["symbol"]).upper(),
                side=str(row["side"]).lower(),  # type: ignore[arg-type]
                shares=float(row["shares"]),
                price=float(row["price"]),
                fee=float(row["fee"]),
                cash_after=float(row["cash_after"]),
                avg_cost_before=float(ac_before) if ac_before not in (None, "") else None,
                realized_pnl=float(rp) if rp not in (None, "") else None,
                timestamp=ts_str,
            )
        )
    if not avg_cost and txs and positions:
        avg_cost = _replay_avg_cost_from_transactions(txs)

    last_trade_day_str = blob.get("last_trade_day")
    last_trade_day = (
        date.fromisoformat(str(last_trade_day_str))
        if last_trade_day_str not in (None, "")
        else None
    )
    today_bought = {
        str(k).upper(): float(v)
        for k, v in (blob.get("today_bought") or {}).items()
    }
    return Portfolio(
        cash=cash,
        positions=positions,
        transactions=txs,
        fee_rate=fee,
        avg_cost=avg_cost,
        last_trade_day=last_trade_day,
        today_bought=today_bought,
        t_plus_1_enabled=bool(t_plus_1_enabled),
    )


def _replay_avg_cost_from_transactions(txs: list[Transaction]) -> dict[str, float]:
    """从历史成交回放出每个标的的加权平均成本（含手续费）。仅用于旧状态升级。"""
    qty: dict[str, float] = {}
    avg: dict[str, float] = {}
    for t in txs:
        sym = str(t.symbol).upper()
        if t.side == "buy":
            old_q = qty.get(sym, 0.0)
            old_a = avg.get(sym, 0.0)
            cost_with_fee = t.shares * t.price + (t.fee or 0.0)
            new_q = old_q + t.shares
            if new_q > 0:
                avg[sym] = (old_q * old_a + cost_with_fee) / new_q
                qty[sym] = new_q
        elif t.side == "sell":
            old_q = qty.get(sym, 0.0)
            new_q = old_q - t.shares
            if new_q <= 1e-9:
                qty.pop(sym, None)
                avg.pop(sym, None)
            else:
                qty[sym] = new_q
    return avg


def save_live_portfolio(path: Path, portfolio: Portfolio) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "cash": portfolio.cash,
        "fee_rate": portfolio.fee_rate,
        "positions": {k: float(v) for k, v in portfolio.positions.items()},
        "avg_cost": {k: float(v) for k, v in portfolio.avg_cost.items()},
        "last_trade_day": (
            portfolio.last_trade_day.isoformat() if portfolio.last_trade_day else None
        ),
        "today_bought": {k: float(v) for k, v in portfolio.today_bought.items()},
        "transactions": [
            {
                "day": t.day.isoformat(),
                "symbol": t.symbol,
                "side": t.side,
                "shares": t.shares,
                "price": t.price,
                "fee": t.fee,
                "cash_after": t.cash_after,
                "avg_cost_before": t.avg_cost_before,
                "realized_pnl": t.realized_pnl,
                "timestamp": getattr(t, "timestamp", None),
            }
            for t in portfolio.transactions
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_live_equity_csv(
    settings: Settings,
    *,
    ts: datetime,
    decision_day: date,
    phase_key: str,
    portfolio: Portfolio,
    mark_prices: dict[str, float],
) -> None:
    prefix = settings.live_equity_csv_prefix.strip() or "live_intraday"
    csv_path = settings.data_dir / f"{prefix}_equity.csv"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    ic = float(settings.initial_capital)
    eq = portfolio.equity(mark_prices)
    ret_pct = (eq / ic - 1.0) * 100.0 if ic > 0 else 0.0
    row = {
        "datetime": ts.isoformat(timespec="seconds"),
        "date": decision_day.isoformat(),
        "phase": phase_key,
        "cash": portfolio.cash,
        "equity": eq,
        "return_pct": ret_pct,
    }
    df_new = pd.DataFrame([row])
    if csv_path.is_file():
        old = pd.read_csv(csv_path)
        df_out = pd.concat([old, df_new], ignore_index=True)
    else:
        df_out = df_new
    df_out.to_csv(csv_path, index=False)


def run_live_intraday_phase(settings: Settings, *, phase_key: str) -> None:
    if phase_key not in PHASE_BY_KEY:
        print(f"未知 phase，可选：{list(PHASE_BY_KEY)}", file=sys.stderr)
        sys.exit(2)
    if settings.strategy_mode.strip().lower() != "llm":
        print("live 节点需要 STRATEGY_MODE=llm", file=sys.stderr)
        sys.exit(2)

    now = _shanghai_now()
    if now.weekday() >= 5:
        print(f"[live] 周末跳过 ({phase_key})")
        return

    decision_day = now.date()
    phase_cn = PHASE_BY_KEY[phase_key]
    free = settings.is_free_selection()
    fixed_syms = settings.symbols()
    if not free and not fixed_syms:
        print("fixed 模式需要非空 UNIVERSE", file=sys.stderr)
        sys.exit(2)

    universe: set[str] = set(fixed_syms) if not free else set()
    cal = settings.calendar_symbol.strip().upper()
    bms = settings.reference_benchmark_symbols()

    def _dedupe(sym_list: list[str]) -> list[str]:
        return list(dict.fromkeys([x for x in sym_list if x]))

    if free:
        boot_symbols = _dedupe([cal, *bms, *fixed_syms])
    else:
        boot_symbols = _dedupe([*bms, *fixed_syms])

    state_path = Path(settings.live_portfolio_state_path)
    portfolio = load_live_portfolio(
        state_path,
        initial_capital=float(settings.initial_capital),
        fee_rate=float(settings.commission_rate),
        t_plus_1_enabled=bool(settings.t_plus_1_enabled),
    )

    start_d = (decision_day - timedelta(days=420)).isoformat()
    end_d = decision_day.isoformat()
    cache_dir = settings.data_dir

    df = download_prices(boot_symbols, start_d, end_d, cache_dir=cache_dir)
    if df.empty:
        print("[live] 下载行情失败，跳过", file=sys.stderr)
        return

    panel = df
    ts_pd = pd.Timestamp(decision_day)
    idx = panel.index
    pos = idx.searchsorted(ts_pd, side="right") - 1
    if pos < 0:
        print("[live] 行情索引不足以覆盖决策日", file=sys.stderr)
        return
    ts_pd = pd.Timestamp(idx[pos])

    panel = ensure_panel_has_symbols(panel, list(portfolio.positions.keys()), start_d, end_d, cache_dir)
    if not free and fixed_syms:
        panel = ensure_panel_has_symbols(panel, fixed_syms, start_d, end_d, cache_dir)
    panel = ensure_panel_has_symbols(panel, bms, start_d, end_d, cache_dir)

    others = list(dict.fromkeys([*portfolio.positions.keys(), *fixed_syms, cal]))
    others = [w for w in others if w and w not in set(bms)]
    watch_trimmed = bms + others[: max(0, 27 - len(bms))]

    candidate_symbols: list[str] = []
    candidate_text = ""
    auto_quotes: dict[str, float] = {}

    panel = ensure_panel_has_symbols(panel, watch_trimmed, start_d, end_d, cache_dir)
    if free:
        scan_syms = settings.market_scan_symbols()
        if scan_syms:
            panel = ensure_panel_has_symbols(panel, scan_syms, start_d, end_d, cache_dir)
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

    prices_bar = latest_row(panel, ts_pd)

    probe_syms = list(
        dict.fromkeys(
            [
                *candidate_symbols[: min(20, len(candidate_symbols))],
                *watch_trimmed,
                *list(portfolio.positions.keys()),
            ]
        )
    )

    exec_prices = build_live_execution_prices(
        probe_syms,
        period=str(settings.intraday_quote_period),
        interval=str(settings.intraday_quote_interval),
        http_proxy=settings.market_scan_http_proxy,
        https_proxy=settings.market_scan_https_proxy,
        no_proxy=settings.market_scan_no_proxy,
    )
    if free and auto_quotes:
        for k, v in auto_quotes.items():
            if float(v) > 0:
                exec_prices[str(k).upper()] = float(v)

    mtm_prices = dict(prices_bar)
    for sym, px in exec_prices.items():
        if float(px) > 0:
            mtm_prices[sym] = float(px)

    equity_mtm = portfolio.equity(mtm_prices)
    intraday_quotes_text = json.dumps(exec_prices, ensure_ascii=False) if exec_prices else ""

    ext_ctx = fetch_external_context(
        settings,
        decision_day=str(decision_day),
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
            assistant_watchlist = list(dict.fromkeys([*candidate_symbols[:15], *watch_trimmed]))
            panel = ensure_panel_has_symbols(panel, assistant_watchlist, start_d, end_d, cache_dir)
        day_dir = Path(settings.assistant_artifacts_dir) / str(decision_day)
        day_dir.mkdir(parents=True, exist_ok=True)
        intraday_bundle, _used_fb = run_single_intraday_phase(
            settings,
            phase_key=phase_key,
            phase_cn=phase_cn,
            panel=panel,
            ts=ts_pd,
            decision_day=decision_day,
            watchlist=assistant_watchlist,
            portfolio=portfolio,
            prices=mtm_prices,
            benchmarks=bms,
            day_dir=day_dir,
            live_execution_prices=exec_prices,
        )
        max_chars = int(settings.assistant_max_bundle_chars)
        if len(intraday_bundle) > max_chars:
            intraday_bundle = (
                intraday_bundle[:max_chars]
                + "\n\n…(单节点看盘助手已截断，完整见 ASSISTANT_ARTIFACTS_DIR 当日目录)\n"
            )

    recent = _fmt_recent_bars(panel, watch_trimmed, ts_pd)
    bench_text = format_benchmark_prices(mtm_prices, bms)

    user = build_user_prompt(
        watchlist=watch_trimmed,
        free_selection=free,
        day=str(decision_day),
        cash=portfolio.cash,
        equity=equity_mtm,
        positions=dict(portfolio.positions),
        prices=mtm_prices,
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

    fill_prices = dict(exec_prices)
    for sym, px in prices_bar.items():
        fill_prices.setdefault(sym, px)

    try:
        decision = glm_decision_sync(
            settings,
            system_prompt=system_prompt_for(settings.selection_mode),
            user_prompt=user,
        )
        actions = decision.get("actions") if isinstance(decision, dict) else []
        if not isinstance(actions, list):
            actions = []

        executable_actions: list[dict[str, Any]] = actions
        if free and bool(settings.free_selection_enforce_candidates):
            cand_set = set(candidate_symbols)
            hold_set = set(portfolio.positions.keys())
            executable_actions = []
            for a in actions:
                if not isinstance(a, dict):
                    continue
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
            if not isinstance(a, dict):
                continue
            s = str(a.get("symbol", "")).upper().strip()
            if s:
                action_syms.append(s)

        panel = ensure_panel_has_symbols(panel, action_syms, start_d, end_d, cache_dir)
        bar_fill = latest_row(panel, ts_pd)
        for sym, px in bar_fill.items():
            if fill_prices.get(sym, 0) <= 0 and px > 0:
                fill_prices[sym] = px

        missing_exec = [s for s in action_syms if fill_prices.get(s, 0) <= 0]
        if missing_exec:
            extra = build_live_execution_prices(
                missing_exec,
                period=str(settings.intraday_quote_period),
                interval=str(settings.intraday_quote_interval),
                http_proxy=settings.market_scan_http_proxy,
                https_proxy=settings.market_scan_https_proxy,
                no_proxy=settings.market_scan_no_proxy,
            )
            fill_prices.update({k: v for k, v in extra.items() if v > 0})

        _apply_actions(
            portfolio,
            executable_actions,
            fill_prices,
            universe,
            decision_day,
            float(settings.max_position_fraction),
            int(settings.lot_size),
            free_selection=free,
            ts=now,
        )
    except Exception as exc:
        print(f"[live] DeepSeek/撮合异常（本轮不调仓）：{type(exc).__name__}: {exc}", file=sys.stderr)

    save_live_portfolio(state_path, portfolio)
    append_live_equity_csv(
        settings,
        ts=now,
        decision_day=decision_day,
        phase_key=phase_key,
        portfolio=portfolio,
        mark_prices=fill_prices,
    )
    prefix = settings.live_equity_csv_prefix.strip() or "live_intraday"
    write_transactions_csv(portfolio, settings.data_dir / f"{prefix}_transactions.csv")

    eq_final = portfolio.equity(fill_prices)
    print(
        f"[live] {phase_key} {decision_day} 权益≈{eq_final:.2f} "
        f"现金={portfolio.cash:.2f} 持仓={len(portfolio.positions)} "
        f"状态→{state_path}"
    )

    if settings.cache_prune_enabled:
        from invest_system.cache_janitor import prune_data_cache

        try:
            stats = prune_data_cache(
                Path(settings.data_dir),
                max_age_days=int(settings.cache_prune_max_age_days),
            )
            if stats.get("removed"):
                mb = stats["bytes_freed"] / (1024 * 1024)
                print(f"[live] 缓存清理：删除 {stats['removed']} 个 pkl，释放 {mb:.2f} MB")
        except Exception as exc:
            print(f"[live] 缓存清理异常：{type(exc).__name__}: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="盘中单节点：实盘参考价 + 调仓（持久化模拟盘）")
    p.add_argument(
        "--phase",
        required=True,
        choices=[k for k, _ in INTRADAY_PHASES],
        help="对应 INTRADAY_PHASES：pre_open / open_5m / midday / close",
    )
    args = p.parse_args(argv)
    run_live_intraday_phase(load_settings(), phase_key=args.phase)


if __name__ == "__main__":
    main()
