from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Runtime-configurable parameters (modified by evolution system)
# ---------------------------------------------------------------------------
_SCANNER_PARAMS: dict[str, float] = {
    "scanner_score_weight_chg": 0.65,
    "scanner_score_weight_amt": 0.35,
}


def get_scanner_params() -> dict[str, float]:
    return dict(_SCANNER_PARAMS)


def set_scanner_params(params: dict[str, float]) -> None:
    _SCANNER_PARAMS.update(params)


def _to_yahoo_symbol(code: str) -> str:
    c = (code or "").strip()
    low = c.lower()
    if low.startswith(("sh", "sz", "bj")) and len(c) >= 8:
        c = c[2:]
    if len(c) != 6 or not c.isdigit():
        return ""
    if c.startswith(("6", "9")):
        return f"{c}.SS"
    if c.startswith(("0", "3")):
        return f"{c}.SZ"
    return ""


def _to_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


@contextmanager
def _temporary_proxy_env(http_proxy: str, https_proxy: str, no_proxy: str):
    keys = ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"]
    old = {k: os.environ.get(k) for k in keys}
    try:
        if http_proxy.strip():
            os.environ["HTTP_PROXY"] = http_proxy.strip()
            os.environ["http_proxy"] = http_proxy.strip()
        if https_proxy.strip():
            os.environ["HTTPS_PROXY"] = https_proxy.strip()
            os.environ["https_proxy"] = https_proxy.strip()
        if no_proxy.strip():
            os.environ["NO_PROXY"] = no_proxy.strip()
            os.environ["no_proxy"] = no_proxy.strip()
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _probe_connectivity(
    probe_url: str,
    *,
    timeout_sec: float,
    http_proxy: str,
    https_proxy: str,
) -> str:
    url = probe_url.strip()
    if not url:
        return ""
    proxies: dict[str, str] = {}
    if http_proxy.strip():
        proxies["http"] = http_proxy.strip()
    if https_proxy.strip():
        proxies["https"] = https_proxy.strip()
    try:
        r = requests.get(
            url,
            timeout=max(0.5, float(timeout_sec)),
            proxies=proxies or None,
            allow_redirects=True,
        )
        return "" if r.status_code < 500 else f"probe_http_{r.status_code}"
    except Exception as exc:
        return f"probe_{type(exc).__name__}"


def _scan_from_hot_rank(top_n: int) -> tuple[list[str], str, dict[str, float]]:
    import akshare as ak

    hot = ak.stock_hot_rank_em()
    if hot is None or hot.empty:
        return [], "", {}
    if "代码" not in hot.columns:
        return [], "", {}
    df = hot.copy()
    df["symbol"] = df["代码"].astype(str).map(_to_yahoo_symbol)
    df = df[df["symbol"] != ""].copy()
    if df.empty:
        return [], "", {}
    df["chg_pct"] = df["涨跌幅"].map(_to_float) if "涨跌幅" in df.columns else 0.0
    df["last_px"] = df["最新价"].map(_to_float) if "最新价" in df.columns else 0.0
    # 榜单前排优先；涨幅仅做轻权重
    if "当前排名" in df.columns:
        df["rank_num"] = df["当前排名"].map(_to_float)
        df = df[df["rank_num"] > 0]
        df["rank_score"] = 1.0 / df["rank_num"]
    else:
        df["rank_score"] = 0.0
    df["score"] = df["rank_score"] * 0.8 + df["chg_pct"] * 0.2
    top = df.sort_values("score", ascending=False).head(top_n).copy()
    symbols = top["symbol"].astype(str).tolist()
    quotes = {r["symbol"]: float(r["last_px"]) for _, r in top.iterrows() if float(r["last_px"]) > 0}
    lines = [
        "strict_mode=true（free 模式仅允许候选池买入；卖出现有持仓不受限）",
        "source=akshare.stock_hot_rank_em",
        f"candidates={symbols}",
    ]
    for _, r in top.head(15).iterrows():
        lines.append(
            f"{r['symbol']}: score={float(r['score']):.3f}, hot_rank={int(float(r.get('rank_num', 0)) or 0)}, "
            f"chg={float(r['chg_pct']):.2f}%, px={float(r['last_px']):.2f}"
        )
    return symbols, "\n".join(lines), quotes


def _scan_from_all_spot_legacy(top_n: int) -> tuple[list[str], str, dict[str, float]]:
    import akshare as ak

    spot = ak.stock_zh_a_spot()
    if spot is None or spot.empty:
        return [], "", {}
    code_col = "code" if "code" in spot.columns else ("代码" if "代码" in spot.columns else "")
    if not code_col:
        return [], "", {}
    df = spot.copy()
    df["symbol"] = df[code_col].astype(str).map(_to_yahoo_symbol)
    df = df[df["symbol"] != ""].copy()
    if df.empty:
        return [], "", {}

    # 兼容两套字段：legacy 英文 与 中文列
    chg_col = "changepercent" if "changepercent" in df.columns else ("涨跌幅" if "涨跌幅" in df.columns else "")
    amt_col = "amount" if "amount" in df.columns else ("成交额" if "成交额" in df.columns else "")
    px_col = "trade" if "trade" in df.columns else ("最新价" if "最新价" in df.columns else "")
    df["chg_pct"] = df[chg_col].map(_to_float) if chg_col else 0.0
    df["amount"] = df[amt_col].map(_to_float) if amt_col else 0.0
    df["last_px"] = df[px_col].map(_to_float) if px_col else 0.0
    df = df[(df["last_px"] > 0) & (df["amount"] >= 0)].copy()
    if df.empty:
        return [], "", {}

    liq_n = max(top_n * 8, 120)
    liquid = df.sort_values("amount", ascending=False).head(liq_n).copy()
    liquid["rank_chg"] = liquid["chg_pct"].rank(pct=True)
    liquid["rank_amt"] = liquid["amount"].rank(pct=True)
    w_chg = _SCANNER_PARAMS["scanner_score_weight_chg"]
    w_amt = _SCANNER_PARAMS["scanner_score_weight_amt"]
    liquid["score"] = liquid["rank_chg"] * w_chg + liquid["rank_amt"] * w_amt
    top = liquid.sort_values("score", ascending=False).head(top_n).copy()

    symbols = top["symbol"].astype(str).tolist()
    quotes = {r["symbol"]: float(r["last_px"]) for _, r in top.iterrows() if float(r["last_px"]) > 0}
    lines = [
        "strict_mode=true（free 模式仅允许候选池买入；卖出现有持仓不受限）",
        "source=akshare.stock_zh_a_spot",
        f"candidates={symbols}",
    ]
    for _, r in top.head(15).iterrows():
        lines.append(
            f"{r['symbol']}: score={float(r['score']):.3f}, chg={float(r['chg_pct']):.2f}%, "
            f"amt={float(r['amount'])/1e8:.2f}亿, px={float(r['last_px']):.2f}"
        )
    return symbols, "\n".join(lines), quotes


def _read_cache(
    cache_file: Path,
    max_age_min: int,
) -> tuple[list[str], str, dict[str, float]]:
    if not cache_file.is_file():
        return [], "", {}
    try:
        blob = json.loads(cache_file.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(str(blob.get("generated_at", "")))
        if datetime.now() - ts > timedelta(minutes=max(1, int(max_age_min))):
            return [], "", {}
        syms = [str(x).upper() for x in blob.get("symbols", []) if isinstance(x, str)]
        text = str(blob.get("text", "")).strip()
        quotes_raw = blob.get("quotes", {})
        quotes = {}
        if isinstance(quotes_raw, dict):
            for k, v in quotes_raw.items():
                px = _to_float(v)
                if px > 0:
                    quotes[str(k).upper()] = px
        if syms and text:
            return syms, text, quotes
    except Exception:
        return [], "", {}
    return [], "", {}


def _write_cache(
    cache_file: Path,
    symbols: list[str],
    text: str,
    quotes: dict[str, float],
) -> None:
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "symbols": symbols,
            "text": text,
            "quotes": quotes,
        }
        cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def spot_prices_for_yahoo_symbols(
    yahoo_symbols: list[str],
    *,
    http_proxy: str = "",
    https_proxy: str = "",
    no_proxy: str = "",
) -> dict[str, float]:
    """从东财 A 股全行情快照中抓取指定 Yahoo 代码的最新价（实盘撮合补充）。"""
    want = {s.strip().upper() for s in yahoo_symbols if s.strip()}
    if not want:
        return {}
    out: dict[str, float] = {}
    with _temporary_proxy_env(http_proxy, https_proxy, no_proxy):
        try:
            import akshare as ak
        except ImportError:
            return {}

        def _from_df(df: pd.DataFrame) -> None:
            nonlocal out
            if df is None or df.empty or "代码" not in df.columns:
                return
            chunk = df.copy()
            chunk["symbol"] = chunk["代码"].astype(str).map(_to_yahoo_symbol)
            chunk = chunk[chunk["symbol"].isin(want)].copy()
            if chunk.empty:
                return
            px_col = "最新价" if "最新价" in chunk.columns else ""
            if not px_col:
                return
            chunk["last_px"] = chunk[px_col].map(_to_float)
            for _, r in chunk.iterrows():
                sym = str(r["symbol"]).upper()
                px = float(r["last_px"])
                if sym and px > 0:
                    out[sym] = px

        try:
            spot = ak.stock_zh_a_spot_em()
            _from_df(spot)
        except Exception:
            pass
        missing = want - set(out.keys())
        if missing:
            try:
                leg = ak.stock_zh_a_spot()
                if leg is not None and not leg.empty:
                    code_col = "code" if "code" in leg.columns else ("代码" if "代码" in leg.columns else "")
                    px_col = "trade" if "trade" in leg.columns else ("最新价" if "最新价" in leg.columns else "")
                    if code_col and px_col:
                        t = leg.copy()
                        t["symbol"] = t[code_col].astype(str).map(_to_yahoo_symbol)
                        t = t[t["symbol"].isin(missing)].copy()
                        t["last_px"] = t[px_col].map(_to_float)
                        for _, r in t.iterrows():
                            sym = str(r["symbol"]).upper()
                            px = float(r["last_px"])
                            if sym and px > 0:
                                out[sym] = px
            except Exception:
                pass
    return out


def scan_cn_candidates_with_akshare(
    top_n: int,
    *,
    retries: int = 3,
    cache_file: Path | None = None,
    cache_max_age_min: int = 240,
    http_proxy: str = "",
    https_proxy: str = "",
    no_proxy: str = "",
    probe_url: str = "",
    probe_timeout_sec: float = 3.0,
) -> tuple[list[str], str, dict[str, float]]:
    """Auto-scan A-share candidates from akshare realtime board.

    Returns:
      - candidate symbols (Yahoo format)
      - prompt-friendly text summary
      - intraday quote snapshot dict
    """
    if top_n <= 0:
        return [], "", {}
    with _temporary_proxy_env(http_proxy, https_proxy, no_proxy):
        try:
            import akshare as ak
        except ImportError:
            return [], "自动扫描不可用：未安装 akshare。", {}

        probe_err = _probe_connectivity(
            probe_url,
            timeout_sec=probe_timeout_sec,
            http_proxy=http_proxy,
            https_proxy=https_proxy,
        )

        last_err = "unknown"
        spot = None
        for i in range(max(1, int(retries))):
            try:
                spot = ak.stock_zh_a_spot_em()
                if spot is not None and not spot.empty:
                    break
                last_err = "empty_data"
            except Exception as exc:
                last_err = type(exc).__name__
            if i < max(1, int(retries)) - 1:
                time.sleep(min(2.0, 0.5 + 0.5 * i))
        if spot is None or spot.empty:
            # 备用方案：东方财富热股榜
            try:
                syms, text, quotes = _scan_from_hot_rank(top_n)
                if syms and text:
                    if cache_file is not None:
                        _write_cache(cache_file, syms, text, quotes)
                    if probe_err:
                        text = f"{text}\n{probe_err}=true"
                    return syms, text, quotes
            except Exception as exc:
                last_err = f"{last_err} | hot_rank:{type(exc).__name__}"
            # 再备用：全市场老接口（更慢但通常更稳定）
            try:
                syms, text, quotes = _scan_from_all_spot_legacy(top_n)
                if syms and text:
                    if cache_file is not None:
                        _write_cache(cache_file, syms, text, quotes)
                    if probe_err:
                        text = f"{text}\n{probe_err}=true"
                    return syms, text, quotes
            except Exception as exc:
                last_err = f"{last_err} | legacy_spot:{type(exc).__name__}"
            if cache_file is not None:
                syms, text, quotes = _read_cache(cache_file, cache_max_age_min)
                if syms and text:
                    return syms, f"{text}\n(cache_fallback=true)", quotes
            if probe_err:
                return [], f"自动扫描失败：{last_err} | {probe_err}", {}
            return [], f"自动扫描失败：{last_err}", {}

    df = spot.copy()
    if "代码" not in df.columns:
        return [], "自动扫描失败：数据缺少`代码`列。", {}
    df["symbol"] = df["代码"].astype(str).map(_to_yahoo_symbol)
    df = df[df["symbol"] != ""].copy()
    if df.empty:
        return [], "自动扫描失败：未识别到可交易 A 股代码。", {}

    df["chg_pct"] = df["涨跌幅"].map(_to_float) if "涨跌幅" in df.columns else 0.0
    df["amount"] = df["成交额"].map(_to_float) if "成交额" in df.columns else 0.0
    df["last_px"] = df["最新价"].map(_to_float) if "最新价" in df.columns else 0.0
    df = df[(df["last_px"] > 0) & (df["amount"] >= 0)].copy()
    if df.empty:
        return [], "自动扫描失败：有效报价为空。", {}

    # First keep liquid names, then pick momentum leaders.
    liq_n = max(top_n * 8, 120)
    liquid = df.sort_values("amount", ascending=False).head(liq_n).copy()
    liquid["rank_chg"] = liquid["chg_pct"].rank(pct=True)
    liquid["rank_amt"] = liquid["amount"].rank(pct=True)
    w_chg = _SCANNER_PARAMS["scanner_score_weight_chg"]
    w_amt = _SCANNER_PARAMS["scanner_score_weight_amt"]
    liquid["score"] = liquid["rank_chg"] * w_chg + liquid["rank_amt"] * w_amt
    top = liquid.sort_values("score", ascending=False).head(top_n).copy()

    symbols = top["symbol"].astype(str).tolist()
    quotes = {r["symbol"]: float(r["last_px"]) for _, r in top.iterrows() if float(r["last_px"]) > 0}
    lines = [
        "strict_mode=true（free 模式仅允许候选池买入；卖出现有持仓不受限）",
        "source=akshare.stock_zh_a_spot_em",
        f"candidates={symbols}",
    ]
    for _, r in top.head(15).iterrows():
        lines.append(
            f"{r['symbol']}: score={float(r['score']):.3f}, chg={float(r['chg_pct']):.2f}%, "
            f"amt={float(r['amount'])/1e8:.2f}亿, px={float(r['last_px']):.2f}"
        )
    text = "\n".join(lines)
    if cache_file is not None:
        _write_cache(cache_file, symbols, text, quotes)
    return symbols, text, quotes
