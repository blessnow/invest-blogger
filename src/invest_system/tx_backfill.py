"""根据 equity CSV 中每个 phase 的真实运行时间，对齐 portfolio.transactions 的 timestamp。

运行时机：
- 一次性：``scripts/backfill_tx_timestamps.py``
- 启动自动：``scheduler.py`` 启动 upgrade 阶段会调用一次（幂等）

对齐原理：
- 每个 phase 末尾会 append 一行 equity，其 ``cash`` == 该 phase 最后一笔交易的 ``cash_after``
- 同 phase 内多笔交易共享该 phase 的 ``datetime``
- 对每条交易：当其 ``cash_after`` 与下一个未对齐 equity 行的 ``cash`` 匹配时，把累积的 pending 全部贴上该 datetime
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from invest_system.portfolio import Portfolio, Transaction

CASH_TOL = 0.5  # 元；浮点对账容忍


def _align_for_day(txs: list[Transaction], eq_day: pd.DataFrame) -> int:
    if eq_day.empty or not txs:
        return 0
    eq_day = eq_day.sort_values("datetime", kind="mergesort").reset_index(drop=True)
    n_eq = len(eq_day)
    i = 0
    pending: list[Transaction] = []
    rewritten = 0
    for tx in txs:
        pending.append(tx)
        while i < n_eq:
            eq_cash = float(eq_day.loc[i, "cash"])
            if abs(float(tx.cash_after) - eq_cash) <= CASH_TOL:
                ts = str(eq_day.loc[i, "datetime"])
                for p in pending:
                    p.timestamp = ts
                    rewritten += 1
                pending = []
                i += 1
                break
            # 空 phase（cash 与上一行相同）→ 安全跳过
            if i > 0 and abs(eq_cash - float(eq_day.loc[i - 1, "cash"])) <= CASH_TOL:
                i += 1
                continue
            break
    return rewritten


def align_transactions_to_equity(
    portfolio: Portfolio,
    equity_csv: Path,
) -> int:
    """对 portfolio 内存里的 transactions 做 timestamp 对齐。返回成功改写条数。"""
    if not equity_csv.is_file() or not portfolio.transactions:
        return 0
    try:
        eq_df = pd.read_csv(equity_csv)
    except Exception:
        return 0
    if "date" not in eq_df.columns or "cash" not in eq_df.columns or "datetime" not in eq_df.columns:
        return 0
    eq_df["date"] = eq_df["date"].astype(str)

    # 按 day 分组
    days: dict[str, list[Transaction]] = {}
    for tx in portfolio.transactions:
        days.setdefault(tx.day.isoformat(), []).append(tx)

    total = 0
    for day_str, txs in days.items():
        eq_day = eq_df[eq_df["date"] == day_str]
        total += _align_for_day(txs, eq_day)
    return total


def iter_aligned_summary(portfolio: Portfolio) -> Iterable[tuple[str, str, str, str]]:
    """调试用：返回每条交易的 (day, symbol, side, timestamp)。"""
    for t in portfolio.transactions:
        yield (t.day.isoformat(), t.symbol, t.side, t.timestamp or "")
