"""rotation 策略实盘节点 — 每天 14:45 调用一次。

执行流程：
  1. 加载本地 panels（含 ETF 历史 OHLCV）
  2. 用 akshare fund_etf_spot_em 拉 31 只候选 ETF 的当日最新价
  3. 把今日价格 append 到 panel 末尾（让 rotation.decide() 看到今天）
  4. 加载 rotation_portfolio_state.json（独立于 LLM 实盘状态）
  5. 调用 rotation.decide() 生成 actions
  6. 通过 broker（paper 或 jvquant）执行
  7. 保存新状态 + 追加 rotation_equity.csv + rotation_transactions.csv

状态文件：
  - data/rotation_portfolio_state.json
  - data/rotation_equity.csv
  - data/rotation_transactions.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from invest_system.broker import Broker, OrderResult, create_broker
from invest_system.config import Settings, load_settings
from invest_system.engine import _apply_actions
from invest_system.errors import log_error
from invest_system.portfolio import Portfolio, Transaction
from invest_system.strategies import rotation as _rot


SH_TZ = ZoneInfo("Asia/Shanghai")


def _shanghai_now() -> datetime:
    return datetime.now(SH_TZ)


def _state_path(settings: Settings) -> Path:
    return Path(settings.data_dir) / "rotation_portfolio_state.json"


def _equity_csv(settings: Settings) -> Path:
    return Path(settings.data_dir) / "rotation_equity.csv"


def _tx_csv(settings: Settings) -> Path:
    return Path(settings.data_dir) / "rotation_transactions.csv"


def load_rotation_portfolio(settings: Settings) -> Portfolio:
    """加载 rotation 持仓状态，文件不存在则初始化。"""
    path = _state_path(settings)
    initial = float(settings.initial_capital)
    fee = float(settings.commission_rate)
    t1 = bool(settings.t_plus_1_enabled)
    if not path.is_file():
        return Portfolio(cash=initial, fee_rate=fee, t_plus_1_enabled=t1)
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log_error(component="live_rotation", phase="load_portfolio",
                  error=exc, message=f"rotation 持仓 JSON 解析失败，回退初始资金: {path}")
        return Portfolio(cash=initial, fee_rate=fee, t_plus_1_enabled=t1)

    cash = float(blob.get("cash", initial))
    positions = {str(k).upper(): float(v) for k, v in (blob.get("positions") or {}).items()}
    avg_cost = {str(k).upper(): float(v) for k, v in (blob.get("avg_cost") or {}).items()}
    txs: list[Transaction] = []
    for row in blob.get("transactions") or []:
        if not isinstance(row, dict):
            continue
        try:
            day_obj = date.fromisoformat(str(row["day"]))
        except Exception:
            continue
        txs.append(Transaction(
            day=day_obj,
            symbol=str(row["symbol"]).upper(),
            side=str(row["side"]).lower(),
            shares=float(row["shares"]),
            price=float(row["price"]),
            fee=float(row.get("fee", 0.0)),
            cash_after=float(row.get("cash_after", 0.0)),
            avg_cost_before=row.get("avg_cost_before"),
            realized_pnl=row.get("realized_pnl"),
            timestamp=str(row.get("timestamp")) if row.get("timestamp") else None,
        ))
    last_day_str = blob.get("last_trade_day")
    last_day = date.fromisoformat(last_day_str) if last_day_str else None
    today_bought = {str(k).upper(): float(v) for k, v in (blob.get("today_bought") or {}).items()}
    return Portfolio(
        cash=cash, fee_rate=fee, t_plus_1_enabled=t1,
        positions=positions, avg_cost=avg_cost,
        transactions=txs, last_trade_day=last_day,
        today_bought=today_bought,
    )


def save_rotation_portfolio(settings: Settings, portfolio: Portfolio) -> None:
    path = _state_path(settings)
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


def append_rotation_equity(settings: Settings, *, ts: datetime, portfolio: Portfolio,
                            mark_prices: dict[str, float]) -> None:
    csv_path = _equity_csv(settings)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    ic = float(settings.initial_capital)
    eq = portfolio.equity(mark_prices)
    ret_pct = (eq / ic - 1.0) * 100.0 if ic > 0 else 0.0
    row = {
        "datetime": ts.isoformat(timespec="seconds"),
        "date": ts.date().isoformat(),
        "cash": portfolio.cash,
        "equity": eq,
        "return_pct": ret_pct,
        "num_holdings": len(portfolio.positions),
    }
    df_new = pd.DataFrame([row])
    if csv_path.is_file():
        old = pd.read_csv(csv_path)
        df_out = pd.concat([old, df_new], ignore_index=True)
    else:
        df_out = df_new
    df_out.to_csv(csv_path, index=False)


def _fetch_realtime_etf_prices(etf_yahoo_codes: list[str]) -> dict[str, float]:
    """用 akshare 拉指定 ETF 的当日最新价。返回 {yahoo_code: price}。"""
    try:
        import akshare as ak
        df = ak.fund_etf_spot_em()
    except Exception as exc:
        log_error(component="live_rotation", phase="rt_price",
                  error=exc, message="akshare fund_etf_spot_em 获取失败")
        return {}
    if df is None or df.empty:
        return {}
    out: dict[str, float] = {}
    # 把 31 只 ETF 的 6 位代码 → yahoo
    pure_to_yahoo = {ys.split(".")[0]: ys for ys in etf_yahoo_codes}
    for _, row in df.iterrows():
        code = str(row.get("代码", "")).strip()
        if code in pure_to_yahoo:
            try:
                px = float(row.get("最新价", 0))
                if px > 0:
                    out[pure_to_yahoo[code]] = px
            except (TypeError, ValueError):
                continue
    return out


def _build_panel_with_today(settings: Settings, today: date) -> tuple[pd.DataFrame, dict[str, float]]:
    """构建一个含 ETF 历史 OHLCV + 今日实时价的 panel。

    返回 (panel, today_prices)。
    """
    from invest_system.local_data_loader import build_panel_from_local, load_all_panels

    panels = load_all_panels(Path(settings.data_dir))
    # 历史窗口取近 90 天 + 基准
    start = (today - pd.Timedelta(days=120)).isoformat()
    end = today.isoformat()
    syms = list(dict.fromkeys([
        settings.rotation_regime_symbol if hasattr(settings, "rotation_regime_symbol") else "000300.SS",
        *_rot.ETF_UNIVERSE,
    ]))
    panel, missing = build_panel_from_local(panels, syms, start, end)

    # 基准（HS300）本地没数据，需要 yfinance 拉
    if missing:
        from invest_system.data_feed import ensure_panel_has_symbols
        panel = ensure_panel_has_symbols(panel, missing, start, end, Path(settings.data_dir))

    # 拉今日实时价
    today_prices = _fetch_realtime_etf_prices(_rot.ETF_UNIVERSE)

    # 把今日价 append 到 panel 最后一行
    if today_prices:
        ts_today = pd.Timestamp(today)
        # 已有该日数据？删除避免重复
        if ts_today in panel.index:
            panel = panel.drop(ts_today)
        # 构造新行：用今日价填 Close（Open/High/Low 用同值，Volume 留 0）
        new_row_data = {}
        for sym, px in today_prices.items():
            new_row_data[(sym, "Open")] = px
            new_row_data[(sym, "High")] = px
            new_row_data[(sym, "Low")] = px
            new_row_data[(sym, "Close")] = px
            new_row_data[(sym, "Volume")] = 0.0
        new_row = pd.DataFrame([new_row_data], index=[ts_today])
        new_row.columns = pd.MultiIndex.from_tuples(new_row.columns)
        # merge：按列对齐补 NaN
        panel = pd.concat([panel, new_row], axis=0).sort_index()

    return panel, today_prices


def run_live_rotation(settings: Settings, *, broker: Broker | None = None) -> dict[str, Any]:
    """rotation 策略单次实盘调用（14:45 触发）。

    Returns:
        dict: 执行摘要（用于日志/Dashboard 展示）
    """
    now = _shanghai_now()
    summary: dict[str, Any] = {
        "ts": now.isoformat(timespec="seconds"),
        "weekday": now.weekday(),
        "skipped": False,
        "skip_reason": "",
        "actions": [],
        "equity": 0.0,
        "cash": 0.0,
        "num_holdings": 0,
    }

    if now.weekday() >= 5:
        summary["skipped"] = True
        summary["skip_reason"] = "weekend"
        print(f"[rotation] 周末跳过 ({now.date()})")
        return summary

    today = now.date()

    # 1. 加载 portfolio
    portfolio = load_rotation_portfolio(settings)

    # 2. 构建 panel（历史 + 今日实时）
    try:
        panel, today_prices = _build_panel_with_today(settings, today)
    except Exception as exc:
        log_error(component="live_rotation", phase="build_panel",
                  error=exc, message="构建 panel 失败")
        return summary

    if panel.empty or not today_prices:
        log_error(component="live_rotation", phase="build_panel",
                  message=f"panel 空 或 实时价空（panel={panel.shape}, prices={len(today_prices)}）")
        summary["skipped"] = True
        summary["skip_reason"] = "no_data"
        return summary

    # 3. 调用 rotation.decide
    params = _rot.params_from_settings(settings)
    ts_pd = pd.Timestamp(today)
    try:
        actions, _cands = _rot.decide(
            panel=panel, as_of=ts_pd,
            positions=dict(portfolio.positions),
            avg_cost=dict(portfolio.avg_cost),
            cash=portfolio.cash,
            equity=portfolio.equity(today_prices),
            lot_size=int(settings.lot_size),
            fee_rate=float(settings.commission_rate),
            params=params,
        )
    except Exception as exc:
        log_error(component="live_rotation", phase="decide",
                  error=exc, message="rotation.decide 失败")
        return summary

    # 4. 执行
    if actions:
        if broker is None:
            broker_mode = settings.broker_mode.strip().lower()
            broker = create_broker(broker_mode, portfolio, settings)
        universe: set[str] = set(_rot.ETF_UNIVERSE)
        try:
            _apply_actions(
                portfolio, actions, today_prices, universe, today,
                float(settings.max_position_fraction), int(settings.lot_size),
                free_selection=True, ts=now, broker=broker,
            )
        except Exception as exc:
            log_error(component="live_rotation", phase="apply_actions",
                      error=exc, message="撮合失败", extra={"actions": actions})

    # 5. 持久化
    save_rotation_portfolio(settings, portfolio)
    append_rotation_equity(settings, ts=now, portfolio=portfolio, mark_prices=today_prices)
    # 完整交易 CSV
    from invest_system.transactions_io import write_transactions_csv
    write_transactions_csv(portfolio, _tx_csv(settings))

    eq = portfolio.equity(today_prices)
    summary.update({
        "actions": actions,
        "equity": round(eq, 2),
        "cash": round(portfolio.cash, 2),
        "num_holdings": len(portfolio.positions),
    })
    print(
        f"[rotation] {today} 完成 ｜ 权益 {eq:.0f} ｜ 现金 {portfolio.cash:.0f} ｜ "
        f"持仓 {len(portfolio.positions)} 只 ｜ actions: {len(actions)}"
    )
    return summary


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="rotation 策略实盘节点（14:45 调用）")
    p.add_argument("--dry-run", action="store_true", help="只产生 actions，不持久化")
    args = p.parse_args(argv)

    settings = load_settings()
    run_live_rotation(settings)


if __name__ == "__main__":
    main()
