"""标的代码 → 展示名称（用于交易明细等）；带本地 JSON 缓存。"""

from __future__ import annotations

import json
from pathlib import Path

from invest_system.symbols import is_valid_cn_yahoo_symbol

_CN_CODE_TO_NAME: dict[str, str] | None = None


def _load_cn_a_share_map() -> dict[str, str]:
    global _CN_CODE_TO_NAME
    if _CN_CODE_TO_NAME is not None:
        return _CN_CODE_TO_NAME
    import akshare as ak

    df = ak.stock_info_a_code_name()
    df = df.copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    _CN_CODE_TO_NAME = dict(zip(df["code"], df["name"].astype(str).str.strip()))
    return _CN_CODE_TO_NAME


def _yahoo_to_six_digit(sym: str) -> str:
    s = sym.strip().upper()
    if not is_valid_cn_yahoo_symbol(s):
        return ""
    return s.split(".")[0].zfill(6)


def _yfinance_display_name(sym: str) -> str:
    try:
        import yfinance as yf

        info = yf.Ticker(sym).info or {}
        return str(info.get("longName") or info.get("shortName") or "").strip()
    except Exception:
        return ""


def resolve_symbols_to_names(
    symbols: list[str],
    *,
    cache_file: Path | None = None,
) -> dict[str, str]:
    """
    解析 Yahoo 代码（如 600519.SS）为可读名称。
    顺序：本地缓存 → A 股代码表（akshare）→ yfinance（适合 ETF/指数等）。
    """
    uniq = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
    merged: dict[str, str] = {}
    if cache_file and cache_file.is_file():
        try:
            blob = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(blob, dict):
                merged = {str(k).upper(): str(v) for k, v in blob.items()}
        except Exception:
            merged = {}

    cn_map: dict[str, str] | None = None
    changed = False
    out: dict[str, str] = {}

    for sym in uniq:
        cached = merged.get(sym, "").strip()
        if cached:
            out[sym] = cached
            continue

        name = ""
        code = _yahoo_to_six_digit(sym)
        if code:
            cn_map = cn_map or _load_cn_a_share_map()
            name = cn_map.get(code, "").strip()

        if not name:
            name = _yfinance_display_name(sym)

        merged[sym] = name
        out[sym] = name
        changed = True

    if changed and cache_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    return out
