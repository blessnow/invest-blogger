"""主线扫描器 — 用 akshare 历史数据为 mainline 策略提供选股池。

数据来源（akshare）：
  - stock_zt_pool_em(date)            当日涨停池（含连板数、所属行业）
  - stock_zt_pool_previous_em(date)   连板池（昨日已涨停今天再涨停）
  - stock_lhb_detail_em(start, end)   龙虎榜（机构/游资上榜）
  - stock_zh_a_hist(symbol, ...)      个股日K（行情/换手）

按日缓存到 data/mainline_cache/<date>/<kind>.pkl 避免重复请求。

返回结构：每日给出"主线候选股票"列表，含字段：
    {symbol, name, industry, conseq_limits, lhb_net_buy, prior_5d_ret_pct, market_cap_yi}
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class MainlineCandidate:
    symbol: str        # Yahoo 格式 600519.SS / 300750.SZ
    raw_code: str      # 6 位裸代码
    name: str
    industry: str
    conseq_limits: int     # 连板数
    lhb_net_buy_yi: float  # 龙虎榜净买入（亿元）
    prior_5d_ret_pct: float
    market_cap_yi: float   # 流通市值 亿
    score: float = 0.0     # 综合评分（越高越好）


def _to_yahoo(code: str) -> str:
    code = str(code).strip()
    if not code or not code.isdigit() or len(code) != 6:
        return ""
    if code.startswith(("60", "68", "5")):
        return f"{code}.SS"
    if code.startswith(("00", "30", "20")):
        return f"{code}.SZ"
    return ""


def _cache_path(data_dir: Path, day: str, kind: str) -> Path:
    d = data_dir / "mainline_cache" / day
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{kind}.pkl"


def _read_cache(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _write_cache(path: Path, obj: Any) -> None:
    try:
        with path.open("wb") as f:
            pickle.dump(obj, f)
    except Exception:
        pass


def _fmt_date(d: date | str) -> str:
    if isinstance(d, str):
        return d.replace("-", "")
    return d.strftime("%Y%m%d")


_LOCAL_PANELS_CACHE: Any = None


def _try_load_local_panels(data_dir: Path):
    global _LOCAL_PANELS_CACHE
    if _LOCAL_PANELS_CACHE is not None:
        return _LOCAL_PANELS_CACHE
    try:
        from invest_system.local_data_loader import DEFAULT_LOCAL_DIR, load_all_panels

        if not (DEFAULT_LOCAL_DIR / "daily.csv").exists():
            _LOCAL_PANELS_CACHE = False  # 标记已尝试
            return None
        _LOCAL_PANELS_CACHE = load_all_panels(data_dir)
        return _LOCAL_PANELS_CACHE
    except Exception as exc:
        print(f"[mainline_scanner] 本地数据加载失败: {exc}, 降级到 akshare", flush=True)
        _LOCAL_PANELS_CACHE = False
        return None


def fetch_zt_pool(day: date | str, data_dir: Path) -> pd.DataFrame:
    """拉取某日涨停池，优先本地 Tushare 数据，akshare 降级，pickle 缓存。"""
    day_str = _fmt_date(day)
    cache = _cache_path(data_dir, day_str, "zt")
    cached = _read_cache(cache)
    if cached is not None:
        return cached

    # 优先本地数据
    panels = _try_load_local_panels(data_dir)
    if panels:
        from invest_system.local_data_loader import get_zt_pool_for_date, has_data_for_date
        from datetime import datetime as _dt

        d_obj = day if isinstance(day, date) else _dt.strptime(day_str, "%Y%m%d").date()
        if has_data_for_date(panels, d_obj):
            df = get_zt_pool_for_date(panels, d_obj)
            _write_cache(cache, df)
            return df

    # 降级：akshare
    try:
        import akshare as ak

        df = ak.stock_zt_pool_em(date=day_str)
    except Exception:
        df = pd.DataFrame()
    if df is None:
        df = pd.DataFrame()
    _write_cache(cache, df)
    return df


def fetch_lhb(day: date | str, data_dir: Path) -> pd.DataFrame:
    """拉取某日龙虎榜：本地优先，akshare 降级。"""
    day_str = _fmt_date(day)
    cache = _cache_path(data_dir, day_str, "lhb")
    cached = _read_cache(cache)
    if cached is not None:
        return cached

    panels = _try_load_local_panels(data_dir)
    if panels:
        from invest_system.local_data_loader import get_lhb_for_date
        from datetime import datetime as _dt

        d_obj = day if isinstance(day, date) else _dt.strptime(day_str, "%Y%m%d").date()
        df = get_lhb_for_date(panels, d_obj)
        _write_cache(cache, df)
        return df

    try:
        import akshare as ak

        df = ak.stock_lhb_detail_em(start_date=day_str, end_date=day_str)
    except Exception:
        df = pd.DataFrame()
    if df is None:
        df = pd.DataFrame()
    _write_cache(cache, df)
    return df


def build_candidates(
    *,
    decision_day: date,
    data_dir: Path,
    panel: pd.DataFrame | None = None,
    min_conseq_limits: int = 1,
    max_prior5d_ret_pct: float = 60.0,
    min_market_cap_yi: float = 20.0,
    max_market_cap_yi: float = 500.0,
    top_n: int = 8,
    held_symbols: set[str] | None = None,
) -> list[MainlineCandidate]:
    """基于 T-1 收盘后的可得信息构建主线候选股票。

    Args:
        decision_day: 决策日 T（不含），策略只能用 ≤ decision_day-1 的数据
        data_dir: 缓存目录（settings.data_dir）
        panel: yfinance 已有 OHLCV panel，用于校验数据可得性（可选）
        min_conseq_limits: 最低连板数过滤
        max_prior5d_ret_pct: 近5日涨幅过滤（避免远端追高）
        min/max_market_cap_yi: 流通市值区间（亿元）
        top_n: 返回 top-N 候选

    Returns:
        list[MainlineCandidate] 按 score 倒序
    """
    # T-1 是真正可看到涨停池/龙虎榜的日期（避免前视）
    prev = decision_day - timedelta(days=1)
    # 周一往前推到周五
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)

    zt = fetch_zt_pool(prev, data_dir)
    lhb = fetch_lhb(prev, data_dir)
    held_set = held_symbols or set()

    candidates: dict[str, MainlineCandidate] = {}

    # 1) 涨停池中按连板数过滤
    if not zt.empty:
        for _, row in zt.iterrows():
            try:
                conseq = int(row.get("连板数", 1) or 1)
            except (TypeError, ValueError):
                conseq = 1
            if conseq < min_conseq_limits:
                continue
            raw_code = str(row.get("代码", "")).strip()
            sym = _to_yahoo(raw_code)
            if not sym:
                continue
            try:
                mc = float(row.get("流通市值", 0) or 0) / 1e8
            except (TypeError, ValueError):
                mc = 0
            # 持仓股豁免市值上限（避免持仓股因为暴涨破市值上限被强制卖出）
            # mc=0 通常意味着市值数据缺失（如 daily_basic 不覆盖该日）→ 跳过市值过滤
            if mc > 0:
                if sym not in held_set:
                    if mc < min_market_cap_yi or mc > max_market_cap_yi:
                        continue
                else:
                    if mc < min_market_cap_yi * 0.5:  # 持仓股仅过滤极小盘
                        continue
            cand = MainlineCandidate(
                symbol=sym,
                raw_code=raw_code,
                name=str(row.get("名称", "")),
                industry=str(row.get("所属行业", "")),
                conseq_limits=conseq,
                lhb_net_buy_yi=0.0,
                prior_5d_ret_pct=0.0,
                market_cap_yi=mc,
            )
            candidates[sym] = cand

    # 2) 龙虎榜净买入累加
    if not lhb.empty:
        col_buy = "龙虎榜净买额"
        if col_buy not in lhb.columns:
            # 不同 akshare 版本字段名可能不同
            for c in lhb.columns:
                if "净买" in c:
                    col_buy = c
                    break
        for _, row in lhb.iterrows():
            raw_code = str(row.get("代码", "")).strip()
            sym = _to_yahoo(raw_code)
            if not sym:
                continue
            try:
                net = float(row.get(col_buy, 0) or 0) / 1e8
            except (TypeError, ValueError):
                net = 0.0
            if sym in candidates:
                candidates[sym].lhb_net_buy_yi = net

    # 3) 用 yfinance panel 计算近 5 日涨幅
    if panel is not None and not panel.empty:
        try:
            ts = pd.Timestamp(prev)
            sub = panel.loc[:ts].tail(6)
            for sym, cand in candidates.items():
                if (sym, "Close") not in sub.columns:
                    continue
                closes = sub[(sym, "Close")].dropna()
                if len(closes) < 2:
                    continue
                base = float(closes.iloc[0])
                last = float(closes.iloc[-1])
                if base > 0:
                    cand.prior_5d_ret_pct = (last / base - 1) * 100.0
        except Exception:
            pass

    # 4) 过滤远端 + 主线评分（持仓股豁免远端过滤）
    survivors: list[MainlineCandidate] = []
    industries: dict[str, int] = {}
    for c in candidates.values():
        if c.symbol not in held_set and c.prior_5d_ret_pct > max_prior5d_ret_pct:
            continue
        survivors.append(c)
        industries[c.industry] = industries.get(c.industry, 0) + 1

    # 主线 = 候选股票数最多的行业（top 3）
    top_industries_ordered = sorted(industries.items(), key=lambda x: -x[1])
    top1 = top_industries_ordered[0][0] if top_industries_ordered else ""
    top2 = top_industries_ordered[1][0] if len(top_industries_ordered) > 1 else ""
    top3 = top_industries_ordered[2][0] if len(top_industries_ordered) > 2 else ""

    for c in survivors:
        # 评分：连板数为主导（每板 15 分），主线行业大幅加成，龙虎榜微调
        score = c.conseq_limits * 15.0
        if c.industry == top1:
            score += 30.0  # 第一主线
        elif c.industry == top2:
            score += 15.0  # 第二主线
        elif c.industry == top3:
            score += 8.0
        # 龙虎榜净买入：每 1 亿 +1 分，上限 +10
        score += min(10.0, max(0, c.lhb_net_buy_yi))
        # 小市值溢价（游资偏好）：50-100 亿 +5 分
        if 30 <= c.market_cap_yi <= 150:
            score += 5.0
        # 远端惩罚：近 5 日涨超 30% 扣分
        score -= max(0, c.prior_5d_ret_pct - 30) * 0.5
        c.score = score

    survivors.sort(key=lambda x: -x.score)
    # 持仓股若在 top_n 之外也强制纳入返回（确保 sticky）
    top = survivors[:top_n]
    top_codes = {c.symbol for c in top}
    extras = [c for c in survivors if c.symbol in held_set and c.symbol not in top_codes]
    return top + extras


def candidates_to_dict_list(cands: list[MainlineCandidate]) -> list[dict[str, Any]]:
    return [asdict(c) for c in cands]


def build_strength_pool(
    *,
    decision_day: date,
    data_dir: Path,
    lookback_days: int = 10,
    min_conseq_limits: int = 2,
    min_market_cap_yi: float = 20.0,
    max_market_cap_yi: float = 1500.0,
    held_symbols: set[str] | None = None,
) -> list[MainlineCandidate]:
    """识别"近 N 日强势股"——过去 N 个交易日里有过 ≥min_conseq_limits 板的股票。

    与 build_candidates 不同：不要求 T-1 是涨停日，允许已经回调中。
    用于"回调买入"模型。
    """
    held_set = held_symbols or set()
    pool: dict[str, MainlineCandidate] = {}

    cur = decision_day - timedelta(days=1)
    days_back = 0
    scanned_days = 0
    while scanned_days < lookback_days and days_back < lookback_days * 2 + 5:
        if cur.weekday() < 5:  # 仅交易日
            df = fetch_zt_pool(cur, data_dir)  # 本地优先 + akshare 降级
            if not df.empty:
                scanned_days += 1
                for _, row in df.iterrows():
                    try:
                        conseq = int(row.get("连板数", 1) or 1)
                    except (TypeError, ValueError):
                        conseq = 1
                    if conseq < min_conseq_limits:
                        continue
                    raw_code = str(row.get("代码", "")).strip()
                    sym = _to_yahoo(raw_code)
                    if not sym:
                        continue
                    try:
                        mc = float(row.get("流通市值", 0) or 0) / 1e8
                    except (TypeError, ValueError):
                        mc = 0
                    if mc > 0 and sym not in held_set:
                        if mc < min_market_cap_yi or mc > max_market_cap_yi:
                            continue
                    # 该股是否已在池中？保留更高的连板数 + 更近的天数
                    if sym not in pool or pool[sym].conseq_limits < conseq:
                        pool[sym] = MainlineCandidate(
                            symbol=sym, raw_code=raw_code,
                            name=str(row.get("名称", "")),
                            industry=str(row.get("所属行业", "")),
                            conseq_limits=conseq,
                            lhb_net_buy_yi=0.0,
                            prior_5d_ret_pct=0.0,
                            market_cap_yi=mc,
                            score=0.0,
                        )
                    # 标记最近一次涨停日（用 prior_5d_ret_pct 字段临时存"距今天数"，避免再加字段）
                    if pool[sym].prior_5d_ret_pct == 0 or days_back < pool[sym].prior_5d_ret_pct:
                        pool[sym].prior_5d_ret_pct = days_back  # 字段复用：表示"上次涨停距今天数"
        cur -= timedelta(days=1)
        days_back += 1

    # 评分
    survivors = list(pool.values())
    industries: dict[str, int] = {}
    for c in survivors:
        industries[c.industry] = industries.get(c.industry, 0) + 1
    top_industries = sorted(industries.items(), key=lambda x: -x[1])[:3]
    mainline_set = {ind for ind, _ in top_industries[:1]}
    second_set = {ind for ind, _ in top_industries[1:2]}

    for c in survivors:
        score = c.conseq_limits * 15.0
        # 距今天数惩罚（越近越好）
        days_since = c.prior_5d_ret_pct
        score -= days_since * 2.0
        if c.industry in mainline_set:
            score += 20
        elif c.industry in second_set:
            score += 10
        c.score = score

    survivors.sort(key=lambda x: -x.score)
    return survivors
