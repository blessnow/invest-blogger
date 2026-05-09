from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from invest_system.portfolio import Portfolio
from invest_system.stock_names import resolve_symbols_to_names

# 历史/缺失 timestamp 的回填基准：当天 15:00 (Asia/Shanghai) —— 收盘附近、不与任一 phase 重合，
# 仅保证排序时同日交易能"沉到底"，不会乱掉新写入的精确时点。
_FALLBACK_TIME = time(15, 0, 0)
_FALLBACK_TZ = ZoneInfo("Asia/Shanghai")


def _resolve_ts(t) -> str:
    """返回 ISO 时间戳；缺失则用 day + 15:00:00+08:00 兜底。"""
    raw = getattr(t, "timestamp", None)
    if raw:
        return str(raw)
    dt = datetime.combine(t.day, _FALLBACK_TIME, tzinfo=_FALLBACK_TZ)
    return dt.isoformat(timespec="seconds")


def write_transactions_csv(portfolio: Portfolio, path: Path) -> Path:
    syms = list(dict.fromkeys(t.symbol.upper().strip() for t in portfolio.transactions if t.symbol))
    name_map = resolve_symbols_to_names(
        syms,
        cache_file=path.parent / "stock_name_cache.json",
    )
    rows = []
    for idx, t in enumerate(portfolio.transactions):
        sym_u = t.symbol.upper().strip()
        avg_cost = getattr(t, "avg_cost_before", None)
        realized = getattr(t, "realized_pnl", None)
        realized_pct: float | None = None
        if (
            t.side == "sell"
            and realized is not None
            and avg_cost is not None
            and avg_cost > 0
            and t.shares > 0
        ):
            realized_pct = realized / (avg_cost * t.shares) * 100.0
        rows.append(
            {
                "_seq": idx,  # 用于稳定排序
                "timestamp": _resolve_ts(t),
                "date": t.day,
                "symbol": t.symbol,
                "name": name_map.get(sym_u, ""),
                "side": t.side,
                "shares": t.shares,
                "price": t.price,
                "fee": t.fee,
                "avg_cost_before": avg_cost,
                "realized_pnl": realized,
                "realized_pnl_pct": realized_pct,
                "cash_after": t.cash_after,
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        # 时间倒序（最新在最上）；同时间用插入顺序倒序作为稳定 tie-breaker
        df["_ts_dt"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.sort_values(["_ts_dt", "_seq"], ascending=[False, False])
        df = df.drop(columns=["_ts_dt", "_seq"])
    cols = [
        "timestamp",
        "date",
        "symbol",
        "name",
        "side",
        "shares",
        "price",
        "fee",
        "avg_cost_before",
        "realized_pnl",
        "realized_pnl_pct",
        "cash_after",
    ]
    df = df[[c for c in cols if c in df.columns]]
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path
