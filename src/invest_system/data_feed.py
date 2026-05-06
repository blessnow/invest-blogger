from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import yfinance as yf


def normalize_ticker_price_columns(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance 单标的常为 (Price, Ticker)，多标的常为 (Ticker, Price)；统一为 (Ticker, ×) 以便 (sym, 'Close') 取值。"""
    if df.empty or not isinstance(df.columns, pd.MultiIndex):
        return df
    out = df.copy()
    n0, n1 = out.columns.names[0], out.columns.names[1]
    if n0 == "Price" and n1 == "Ticker":
        out.columns = out.columns.swaplevel(0, 1)
        out.columns.names = ["Ticker", "Price"]
    return out.sort_index(axis=1)


def _normalize_single_download(raw: pd.DataFrame, sym: str) -> pd.DataFrame:
    if raw.empty:
        return raw
    if isinstance(raw.columns, pd.MultiIndex):
        df = raw.copy()
        try:
            df.columns = df.columns.set_levels([sym], level=0)
        except (ValueError, TypeError):
            pass
        return df
    out = raw.copy()
    out.columns = pd.MultiIndex.from_product([[sym], out.columns])
    return out


def fetch_symbol_ohlcv(
    symbol: str,
    start: str,
    end: str,
    cache_dir: Path,
) -> pd.DataFrame:
    sym = symbol.strip().upper()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"ohlcv_{sym}_{start}_{end}.pkl"
    if cache_file.exists():
        return normalize_ticker_price_columns(pd.read_pickle(cache_file))

    raw = yf.download(
        sym,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
    )
    if raw.empty:
        return raw
    df = normalize_ticker_price_columns(_normalize_single_download(raw, sym))
    df.to_pickle(cache_file)
    return df


def merge_price_panels(panel: pd.DataFrame, fragment: pd.DataFrame) -> pd.DataFrame:
    if fragment is None or fragment.empty:
        return panel
    if panel is None or panel.empty:
        out = fragment.copy()
    else:
        out = pd.concat([panel, fragment], axis=1).sort_index()
    if isinstance(out.columns, pd.MultiIndex):
        out = out.sort_index(axis=1)
    return out


def ensure_panel_has_symbols(
    panel: pd.DataFrame,
    symbols: list[str],
    start: str,
    end: str,
    cache_dir: Path,
) -> pd.DataFrame:
    work = panel
    seen: set[str] = set()
    if not work.empty and isinstance(work.columns, pd.MultiIndex):
        seen = set(work.columns.get_level_values(0).astype(str).unique())

    for raw_sym in symbols:
        sym = raw_sym.strip().upper()
        if not sym or sym in seen:
            continue
        frag = fetch_symbol_ohlcv(sym, start, end, cache_dir)
        if frag.empty:
            continue
        work = merge_price_panels(work, frag)
        seen.add(sym)
    return work


def download_prices(
    symbols: list[str],
    start: str,
    end: str,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """
    OHLCV multi-index columns: (Symbol, Field). Index: Datetime.
    Uses Yahoo Finance (real historical prices for paper simulation).
    """
    if not symbols:
        raise ValueError("symbols must be non-empty")

    cache_dir = cache_dir or Path("./data")
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = "_".join(sorted(symbols))[:80]
    cache_file = cache_dir / f"prices_{key}_{start}_{end}.pkl"

    if cache_file.exists():
        return normalize_ticker_price_columns(pd.read_pickle(cache_file))

    if len(symbols) == 1:
        df = fetch_symbol_ohlcv(symbols[0], start, end, cache_dir)
    else:
        raw = yf.download(
            tickers=" ".join(symbols),
            start=start,
            end=end,
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
        if raw.empty:
            raise RuntimeError("No price data returned; check symbols and date range.")
        df = raw

    if df.empty:
        raise RuntimeError("No price data returned; check symbols and date range.")

    df = normalize_ticker_price_columns(df)
    df.to_pickle(cache_file)
    return df


def latest_row(df: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, float]:
    """Close prices for each symbol on or before as_of."""
    if df.empty:
        return {}
    work = df.sort_index(axis=1) if isinstance(df.columns, pd.MultiIndex) else df
    idx = work.index
    pos = idx.searchsorted(as_of, side="right") - 1
    if pos < 0:
        return {}
    row_slice = work.iloc[pos]
    out: dict[str, float] = {}
    for sym in work.columns.get_level_values(0).unique():
        try:
            close = float(row_slice[(sym, "Close")])
            if not math.isfinite(close):
                continue
            out[str(sym)] = close
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fetch_intraday_last_prices(
    symbols: list[str],
    *,
    period: str = "1d",
    interval: str = "1m",
) -> dict[str, float]:
    """Fetch last intraday close for symbols from Yahoo (best-effort)."""
    syms = list(dict.fromkeys([s.strip().upper() for s in symbols if s.strip()]))
    if not syms:
        return {}
    raw = yf.download(
        tickers=" ".join(syms),
        period=period,
        interval=interval,
        group_by="ticker",
        auto_adjust=True,
        threads=True,
        progress=False,
    )
    if raw.empty:
        return {}
    work = raw
    if len(syms) == 1 and not isinstance(work.columns, pd.MultiIndex):
        work = _normalize_single_download(work, syms[0])
    work = normalize_ticker_price_columns(work)
    if not isinstance(work.columns, pd.MultiIndex):
        return {}

    out: dict[str, float] = {}
    for sym in syms:
        try:
            closes = work[(sym, "Close")].dropna()
            if closes.empty:
                continue
            px = float(closes.iloc[-1])
            if math.isfinite(px):
                out[sym] = px
        except (KeyError, TypeError, ValueError):
            continue
    return out


def build_live_execution_prices(
    symbols: list[str],
    *,
    period: str,
    interval: str,
    http_proxy: str = "",
    https_proxy: str = "",
    no_proxy: str = "",
) -> dict[str, float]:
    """实盘撮合参考价：优先 Yahoo 最近一根 K 线收盘价，缺口用东财全市场行情快照补齐。"""
    syms = list(dict.fromkeys([s.strip().upper() for s in symbols if s.strip()]))
    if not syms:
        return {}
    out = fetch_intraday_last_prices(syms, period=period, interval=interval)
    missing = [s for s in syms if out.get(s, 0) <= 0]
    if missing:
        from invest_system.market_scanner import spot_prices_for_yahoo_symbols

        akq = spot_prices_for_yahoo_symbols(
            missing,
            http_proxy=http_proxy,
            https_proxy=https_proxy,
            no_proxy=no_proxy,
        )
        for k, v in akq.items():
            if v > 0:
                out[str(k).upper()] = float(v)
    return out
