from __future__ import annotations

from pathlib import Path

import pandas as pd

from invest_system.portfolio import Portfolio
from invest_system.stock_names import resolve_symbols_to_names


def write_transactions_csv(portfolio: Portfolio, path: Path) -> Path:
    syms = list(dict.fromkeys(t.symbol.upper().strip() for t in portfolio.transactions if t.symbol))
    name_map = resolve_symbols_to_names(
        syms,
        cache_file=path.parent / "stock_name_cache.json",
    )
    rows = []
    for t in portfolio.transactions:
        sym_u = t.symbol.upper().strip()
        rows.append(
            {
                "date": t.day,
                "symbol": t.symbol,
                "name": name_map.get(sym_u, ""),
                "side": t.side,
                "shares": t.shares,
                "price": t.price,
                "fee": t.fee,
                "cash_after": t.cash_after,
            }
        )
    df = pd.DataFrame(rows)
    cols = ["date", "symbol", "name", "side", "shares", "price", "fee", "cash_after"]
    df = df[[c for c in cols if c in df.columns]]
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path
