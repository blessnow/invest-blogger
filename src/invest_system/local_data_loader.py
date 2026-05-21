"""本地 Tushare 数据加载器 — 用于 mainline 策略回测历史区间。

数据源：~/clawd/stock-data/
  - daily.csv          全市场日 K，含 pct_chg → 现场滚动判定涨停（无前视）
  - daily_basic.csv    每日基本面，含 circ_mv 流通市值
  - top_list.csv       龙虎榜（2025-04 起）
  - stock_basic.csv    静态行业表

涨停判定（用户提点：滚动）：
  - 每个 trade_date T，T 当日 pct_chg ≥ 9.5%（科创/创业板 ≥ 19%）即涨停
  - 连板数 = 该股从 T 往回连续涨停的天数
  - 完全用当日及之前数据，无未来信息

首次调用 load_all_panels() 会把 daily.csv 等大文件压缩到 data/local_cache/*.pkl
后续调用直接读 pickle，加载 < 5 秒。
"""

from __future__ import annotations

import os
import pickle
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_LOCAL_DIR = Path(os.path.expanduser("~/clawd/stock-data"))


@dataclass
class LocalPanels:
    daily: pd.DataFrame              # multi-index (ts_code, trade_date) → OHLCV + pct_chg
    daily_basic: pd.DataFrame        # multi-index (ts_code, trade_date) → circ_mv 等
    top_list: pd.DataFrame           # 龙虎榜，按 trade_date 分组
    stock_basic: pd.DataFrame        # ts_code → name, industry, market
    industry_map: dict[str, str]     # ts_code → industry
    name_map: dict[str, str]         # ts_code → name
    etf_daily: pd.DataFrame = None   # ETF 日 K，结构同 daily（含 ts_code, trade_date 索引）
    etf_list: list = None            # [(ts_code, etf_name)] 31 个行业 ETF


def _cache_dir(data_dir: Path) -> Path:
    d = data_dir / "local_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


_PANELS_MEM: LocalPanels | None = None
_ZT_POOL_CACHE: dict = {}      # date → DataFrame
_SYMBOL_DAILY_CACHE: dict = {}  # ts_code → 该股日K切片（按日期排序的 DataFrame）


def load_all_panels(
    data_dir: Path,
    *,
    local_csv_dir: Path = DEFAULT_LOCAL_DIR,
    force_reload: bool = False,
) -> LocalPanels:
    """加载所有本地数据（双层缓存：进程内存 + 磁盘 pickle）。"""
    global _PANELS_MEM
    if _PANELS_MEM is not None and not force_reload:
        return _PANELS_MEM

    cache = _cache_dir(data_dir) / "local_panels.pkl"
    if cache.exists() and not force_reload:
        try:
            with cache.open("rb") as f:
                _PANELS_MEM = pickle.load(f)
                return _PANELS_MEM
        except Exception:
            pass

    print(f"[local_data_loader] 首次加载，从 {local_csv_dir} 读取并缓存...", flush=True)
    # daily
    daily = pd.read_csv(
        local_csv_dir / "daily.csv",
        dtype={"ts_code": str, "trade_date": str},
        usecols=["ts_code", "trade_date", "open", "high", "low", "close",
                 "pre_close", "pct_chg", "vol", "amount"],
    )
    daily["trade_date"] = pd.to_datetime(daily["trade_date"], format="%Y%m%d")
    daily = daily.set_index(["trade_date", "ts_code"]).sort_index()
    print(f"  daily: {len(daily):,} 行 ({daily.index.get_level_values(0).min().date()} ~ {daily.index.get_level_values(0).max().date()})", flush=True)

    # daily_basic（只要市值字段）
    db = pd.read_csv(
        local_csv_dir / "daily_basic.csv",
        dtype={"ts_code": str, "trade_date": str},
        usecols=["ts_code", "trade_date", "circ_mv", "turnover_rate"],
    )
    db["trade_date"] = pd.to_datetime(db["trade_date"], format="%Y%m%d")
    db = db.set_index(["trade_date", "ts_code"]).sort_index()
    print(f"  daily_basic: {len(db):,} 行", flush=True)

    # top_list 龙虎榜
    tl = pd.read_csv(
        local_csv_dir / "top_list.csv",
        dtype={"ts_code": str, "trade_date": str},
    )
    tl["trade_date"] = pd.to_datetime(tl["trade_date"], format="%Y%m%d")
    print(f"  top_list: {len(tl):,} 行 ({tl['trade_date'].min().date()} ~ {tl['trade_date'].max().date()})", flush=True)

    # stock_basic
    sb = pd.read_csv(local_csv_dir / "stock_basic.csv", dtype={"ts_code": str})
    industry_map = dict(zip(sb["ts_code"], sb["industry"].fillna("")))
    name_map = dict(zip(sb["ts_code"], sb["name"].fillna("")))
    print(f"  stock_basic: {len(sb):,} 只 + 行业分类", flush=True)

    # ETF 日 K（30+ 个行业 ETF，用于 rotation 策略）
    etf_path = local_csv_dir / "etf_daily.csv"
    etf_daily = pd.DataFrame()
    etf_list: list = []
    if etf_path.exists():
        etf_daily = pd.read_csv(
            etf_path,
            dtype={"ts_code": str, "trade_date": str},
            usecols=["ts_code", "trade_date", "open", "high", "low", "close",
                     "pre_close", "pct_chg", "vol", "amount", "etf_name"],
        )
        etf_daily["trade_date"] = pd.to_datetime(etf_daily["trade_date"], format="%Y%m%d")
        etf_list = list(etf_daily.drop_duplicates("ts_code")[["ts_code", "etf_name"]].itertuples(index=False, name=None))
        # 记录到 name_map / industry_map（便于通用查询）
        for ts_code, etf_name in etf_list:
            name_map[ts_code] = etf_name
            industry_map[ts_code] = "ETF"
        etf_daily = etf_daily.set_index(["trade_date", "ts_code"]).sort_index()
        print(f"  etf_daily: {len(etf_daily):,} 行, {len(etf_list)} 个 ETF", flush=True)

    panels = LocalPanels(
        daily=daily, daily_basic=db,
        top_list=tl, stock_basic=sb,
        industry_map=industry_map, name_map=name_map,
        etf_daily=etf_daily, etf_list=etf_list,
    )
    try:
        with cache.open("wb") as f:
            pickle.dump(panels, f, protocol=4)
        print(f"  缓存到 {cache} ({cache.stat().st_size / 1e6:.0f} MB)", flush=True)
    except Exception as exc:
        print(f"  缓存失败：{exc}", flush=True)
    _PANELS_MEM = panels
    return panels


def _ts_code_to_yahoo(ts_code: str) -> str:
    """Tushare 代码 (000001.SZ / 600000.SH) → Yahoo 代码 (000001.SZ / 600000.SS)。

    Tushare 用 .SZ/.SH/.BJ，Yahoo 用 .SZ/.SS（北交所 yfinance 一般不支持）。
    """
    code, _, exch = ts_code.partition(".")
    exch = exch.upper()
    if exch == "SH":
        return f"{code}.SS"
    if exch in ("SZ", "BJ"):
        return f"{code}.{exch}"
    return ts_code


def _is_st(name: str) -> bool:
    if not name:
        return False
    n = name.upper().replace(" ", "")
    return n.startswith("*ST") or n.startswith("ST")


def _zt_threshold(code: str, name: str) -> float:
    """根据板块返回涨停阈值（百分比）。

    主板/中小板 10%，创业板/科创板 20%（2020-08 后），北交所 30%（撮合规则不同）
    ST 5%
    """
    if _is_st(name):
        return 4.9  # 5% 阈值，留 0.1 安全边际
    pure = code.split(".")[0]
    if pure.startswith("688") or pure.startswith("300") or pure.startswith("301"):
        return 19.5  # 20%
    if pure.startswith("8") or pure.startswith("4"):  # 北交所
        return 29.5  # 30%
    return 9.5  # 10%


def get_zt_pool_for_date(
    panels: LocalPanels,
    day: _date,
    *,
    lookback_for_conseq: int = 10,
) -> pd.DataFrame:
    """计算指定交易日的涨停池（带内存缓存）。

    返回字段 (兼容 mainline_scanner 中 fetch_zt_pool 的输出):
      代码, 名称, 所属行业, 连板数, 流通市值, 涨跌幅
    """
    cached = _ZT_POOL_CACHE.get(day)
    if cached is not None:
        return cached

    ts = pd.Timestamp(day)
    try:
        sub = panels.daily.loc[ts]
    except KeyError:
        _ZT_POOL_CACHE[day] = pd.DataFrame()
        return _ZT_POOL_CACHE[day]

    # 拿到当日基本面（流通市值）
    try:
        sub_db = panels.daily_basic.loc[ts]
    except KeyError:
        sub_db = pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for ts_code, row in sub.iterrows():
        pct = float(row.get("pct_chg") or 0)
        name = panels.name_map.get(ts_code, "")
        # 排除 ST 股：每日 ±5% 阈值，频繁涨停但风险极高，非真正主线
        if _is_st(name):
            continue
        thr = _zt_threshold(ts_code, name)
        if pct < thr:
            continue
        # 流通市值（亿元）
        mc_yi = 0.0
        if not sub_db.empty and ts_code in sub_db.index:
            cv = sub_db.loc[ts_code].get("circ_mv", 0)
            # Tushare circ_mv 单位是 万元
            mc_yi = float(cv or 0) / 1e4
        # 连板数：回溯 lookback_for_conseq 个交易日
        conseq = _count_conseq_limits(panels, ts_code, ts, lookback_for_conseq, thr)
        rows.append({
            "代码": ts_code.split(".")[0],
            "名称": name,
            "所属行业": panels.industry_map.get(ts_code, ""),
            "连板数": conseq,
            "流通市值": mc_yi * 1e8,  # 兼容 mainline_scanner 输入单位（元）
            "涨跌幅": pct,
        })
    result = pd.DataFrame(rows)
    _ZT_POOL_CACHE[day] = result
    return result


def _get_symbol_daily(panels: LocalPanels, ts_code: str) -> pd.DataFrame:
    """缓存版：取单只股票的日 K（按日期排序）。"""
    cached = _SYMBOL_DAILY_CACHE.get(ts_code)
    if cached is not None:
        return cached
    try:
        sym_data = panels.daily.xs(ts_code, level=1).sort_index()
    except KeyError:
        sym_data = pd.DataFrame()
    _SYMBOL_DAILY_CACHE[ts_code] = sym_data
    return sym_data


def _count_conseq_limits(
    panels: LocalPanels,
    ts_code: str,
    as_of: pd.Timestamp,
    lookback: int,
    threshold_pct: float,
) -> int:
    """从 as_of 往回数：连续 pct_chg ≥ threshold 的天数（含 as_of）。"""
    sym_data = _get_symbol_daily(panels, ts_code)
    if sym_data.empty:
        return 1
    sub = sym_data.loc[:as_of].tail(lookback + 1)
    if sub.empty:
        return 1
    pcts = sub["pct_chg"].astype(float).tolist()
    n = 0
    for p in reversed(pcts):
        if p >= threshold_pct - 0.5:
            n += 1
        else:
            break
    return max(1, n)


def get_lhb_for_date(panels: LocalPanels, day: _date) -> pd.DataFrame:
    """指定交易日的龙虎榜（兼容 mainline_scanner 字段名）。"""
    ts = pd.Timestamp(day)
    sub = panels.top_list[panels.top_list["trade_date"] == ts]
    if sub.empty:
        return pd.DataFrame()
    # 字段重命名以兼容 mainline_scanner._to_yahoo + 净买额读取
    out = pd.DataFrame({
        "代码": sub["ts_code"].apply(lambda x: x.split(".")[0]),
        "名称": sub["name"],
        "龙虎榜净买额": sub["net_amount"],  # 单位元
    })
    return out


def build_panel_from_local(
    panels: LocalPanels,
    yahoo_symbols: list[str],
    start: str,
    end: str,
) -> tuple[pd.DataFrame, list[str]]:
    """把若干 Yahoo 代码的 OHLCV 从本地 daily 数据切出来，返回 (panel, 仍缺失符号)。

    panel 列结构：MultiIndex (Symbol, Field)，Field 为 Open/High/Low/Close/Volume。
    """
    if not yahoo_symbols:
        return pd.DataFrame(), []

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)

    # Yahoo 代码 → Tushare ts_code
    def to_ts_code(yahoo_sym: str) -> str:
        code, _, exch = yahoo_sym.partition(".")
        if exch.upper() == "SS":
            return f"{code}.SH"
        return f"{code}.{exch.upper()}"  # SZ / BJ

    stock_ts = set(panels.daily.index.get_level_values(1).unique())
    etf_ts = set(panels.etf_daily.index.get_level_values(1).unique()) if (panels.etf_daily is not None and not panels.etf_daily.empty) else set()
    found_frames: list[pd.DataFrame] = []
    still_missing: list[str] = []

    # 按来源分组：股票 → daily, ETF → etf_daily
    stock_syms: list[tuple[str, str]] = []
    etf_syms: list[tuple[str, str]] = []
    for ys in yahoo_symbols:
        tc = to_ts_code(ys)
        if tc in stock_ts:
            stock_syms.append((ys, tc))
        elif tc in etf_ts:
            etf_syms.append((ys, tc))
        else:
            still_missing.append(ys)

    def _slice(source: pd.DataFrame, syms: list[tuple[str, str]]) -> None:
        if not syms:
            return
        mask_date = (source.index.get_level_values(0) >= start_ts) & \
                    (source.index.get_level_values(0) <= end_ts)
        sub_all = source[mask_date]
        for ys, tc in syms:
            try:
                sym_df = sub_all.xs(tc, level=1)
            except KeyError:
                still_missing.append(ys)
                continue
            if sym_df.empty:
                still_missing.append(ys)
                continue
            frag = pd.DataFrame({
                (ys, "Open"): sym_df["open"].astype(float),
                (ys, "High"): sym_df["high"].astype(float),
                (ys, "Low"): sym_df["low"].astype(float),
                (ys, "Close"): sym_df["close"].astype(float),
                (ys, "Volume"): sym_df["vol"].astype(float),
            })
            frag.columns = pd.MultiIndex.from_tuples(frag.columns)
            found_frames.append(frag)

    _slice(panels.daily, stock_syms)
    if panels.etf_daily is not None and not panels.etf_daily.empty:
        _slice(panels.etf_daily, etf_syms)

    if not found_frames:
        return pd.DataFrame(), still_missing

    panel = pd.concat(found_frames, axis=1).sort_index()
    return panel, still_missing


def has_data_for_date(panels: LocalPanels, day: _date) -> bool:
    """本地数据是否覆盖该日期。"""
    ts = pd.Timestamp(day)
    try:
        sub = panels.daily.loc[ts]
        return not sub.empty
    except KeyError:
        return False
