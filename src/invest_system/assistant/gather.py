from __future__ import annotations

from email.utils import parsedate_to_datetime
import json
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx

from invest_system.assistant.article_body import fetch_article_excerpt
from invest_system.config import Settings

PRO_DEFAULT_FEEDS = [
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
]


def _is_safe_http_url(url: str) -> bool:
    try:
        p = urlparse(url.strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def _looks_like_xml_feed(body: bytes) -> bool:
    """过滤 HTML 首页等非 RSS/Atom，避免误解析与超时。"""
    sample = body[:65536].lower()
    if b"<rss" in sample or b"<feed" in sample:
        return True
    if b"<?xml" in sample and (b"<channel" in sample or b"<item" in sample):
        return True
    return False


def parse_rss_feed_urls(raw: str, *, max_feeds: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for part in (raw or "").split(","):
        u = part.strip()
        if not u or u in seen:
            continue
        if _is_safe_http_url(u):
            seen.add(u)
            out.append(u)
        if len(out) >= max(1, max_feeds):
            break
    return out


def fetch_rss_headlines(url: str, timeout: float, max_items: int) -> list[dict[str, str]]:
    if not _is_safe_http_url(url):
        return []
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(url.strip())
            r.raise_for_status()
            if not _looks_like_xml_feed(r.content):
                return []
            root = ET.fromstring(r.content)
    except Exception:
        return []

    items: list[dict[str, str]] = []
    # RSS 2.0: channel/item; 部分站点无命名空间
    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        date_el = item.find("pubDate")
        title = (title_el.text or "").strip() if title_el is not None else ""
        link = (link_el.text or "").strip() if link_el is not None else ""
        pub = (date_el.text or "").strip() if date_el is not None else ""
        if title:
            items.append({"title": title, "link": link, "published": pub})
        if len(items) >= max_items:
            break
    return items


def _build_google_news_rss_url(query: str) -> str:
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(query)
        + "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )


def _symbol_to_query(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if "." in s:
        s = s.split(".")[0]
    return s if s else ""


def _dedupe_urls(urls: list[str], max_feeds: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        x = u.strip()
        if not x or x in seen or not _is_safe_http_url(x):
            continue
        seen.add(x)
        out.append(x)
        if len(out) >= max(1, max_feeds):
            break
    return out


def _parse_pub_ts(text: str) -> float:
    t = (text or "").strip()
    if not t:
        return 0.0
    try:
        return parsedate_to_datetime(t).timestamp()
    except Exception:
        return 0.0


def _classify_headline(title: str) -> str:
    t = (title or "").lower()
    if re.search(r"(政策|监管|央行|财政|利率|降准|国常会|美联储|cpi|pmi)", t):
        return "macro_policy"
    if re.search(r"(a股|沪深|上证|深证|北向|两市|成交|指数|市场)", t):
        return "market_flow"
    if re.search(r"(芯片|半导体|算力|ai|人工智能|服务器|存储|gpu)", t):
        return "tech_compute"
    if re.search(r"(业绩|财报|增持|减持|并购|订单|中标)", t):
        return "company_fundamental"
    return "other"


def _headline_sentiment(title: str) -> int:
    t = (title or "").lower()
    pos = ["上涨", "新高", "增持", "中标", "增长", "反弹", "放量", "突破"]
    neg = ["下跌", "暴跌", "回落", "风险", "裁员", "违约", "下滑", "承压"]
    score = sum(1 for w in pos if w in t) - sum(1 for w in neg if w in t)
    return score


def build_news_digest(rss_items: list[dict[str, Any]], *, max_items_per_topic: int) -> dict[str, Any]:
    topics: dict[str, list[dict[str, Any]]] = {
        "macro_policy": [],
        "market_flow": [],
        "tech_compute": [],
        "company_fundamental": [],
        "other": [],
    }
    scores: list[int] = []
    for it in rss_items:
        title = str(it.get("title", ""))
        tp = _classify_headline(title)
        topics.setdefault(tp, []).append(it)
        scores.append(_headline_sentiment(title))

    trimmed = {
        k: v[: max(1, int(max_items_per_topic))]
        for k, v in topics.items()
        if v
    }
    sentiment_score = sum(scores)
    if sentiment_score >= 3:
        sentiment = "risk_on"
    elif sentiment_score <= -3:
        sentiment = "risk_off"
    else:
        sentiment = "neutral"
    return {
        "sentiment": sentiment,
        "sentiment_score": sentiment_score,
        "topic_headlines": trimmed,
        "total_items": len(rss_items),
    }


def gather_all_rss(
    settings: Settings,
    *,
    symbols: list[str] | None = None,
) -> list[dict[str, str]]:
    max_feeds = int(settings.assistant_max_rss_feeds)
    explicit = parse_rss_feed_urls(settings.assistant_rss_urls or "", max_feeds=max_feeds)
    base_urls = explicit if explicit else PRO_DEFAULT_FEEDS
    urls = list(base_urls)
    if settings.assistant_news_search_enabled:
        raw_q = [x.strip() for x in settings.assistant_news_search_queries.split(",") if x.strip()]
        sym_q = [_symbol_to_query(s) for s in (symbols or [])[:5]]
        queries = list(dict.fromkeys([*raw_q, *[q for q in sym_q if q]]))[: max(1, int(settings.assistant_max_search_queries))]
        urls.extend([_build_google_news_rss_url(q) for q in queries])
    urls = _dedupe_urls(urls, max_feeds=max(max_feeds, len(urls)))
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    timeout = float(settings.assistant_http_timeout_sec)
    cap = int(settings.assistant_max_rss_items_total)
    per = max(3, cap // max(len(urls), 1))
    for url in urls:
        for it in fetch_rss_headlines(url, timeout=timeout, max_items=per):
            key = it.get("title", "") + it.get("link", "")
            if key in seen:
                continue
            seen.add(key)
            it["source_feed"] = url
            out.append(it)
            if len(out) >= cap:
                break
    out = sorted(out, key=lambda x: _parse_pub_ts(str(x.get("published", ""))), reverse=True)
    return out[:cap]


def enrich_rss_with_body_excerpts(
    items: list[dict[str, Any]],
    settings: Settings,
) -> list[dict[str, Any]]:
    """Fetch plaintext excerpts from the first N article links (paywalls may yield empty)."""
    if not settings.assistant_fetch_article_body:
        return items
    n = max(0, int(settings.assistant_max_articles_body_fetch))
    max_c = max(200, int(settings.assistant_article_body_max_chars))
    to = float(settings.assistant_article_body_timeout_sec)
    out: list[dict[str, Any]] = []
    for idx, it in enumerate(items):
        row = dict(it)
        link = str(row.get("link", "") or "").strip()
        if idx < n and link and _is_safe_http_url(link):
            row["body_excerpt"] = fetch_article_excerpt(link, timeout_sec=to, max_chars=max_c)
        else:
            row.setdefault("body_excerpt", "")
        out.append(row)
    return out


def fetch_assistant_gather_url(
    settings: Settings,
    *,
    phase: str,
    decision_date: str,
    symbols: list[str],
    positions: dict[str, float],
    cash: float,
    equity: float,
    benchmarks: list[str],
    market_notes: str,
) -> str:
    url = (settings.assistant_gather_url or "").strip()
    if not _is_safe_http_url(url):
        return ""

    payload: dict[str, Any] = {
        "kind": "intraday_assistant",
        "phase": phase,
        "decision_date": decision_date,
        "symbols": symbols,
        "positions": positions,
        "cash": cash,
        "equity": equity,
        "benchmarks": benchmarks,
        "market_notes": market_notes,
    }
    try:
        with httpx.Client(timeout=float(settings.assistant_http_timeout_sec)) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            return _normalize_gather_body(r)
    except Exception:
        return ""


def _normalize_gather_body(r: httpx.Response) -> str:
    ctype = (r.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        data = r.json()
        if isinstance(data, dict):
            for key in ("summary", "text", "context", "markdown"):
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    return v
        return json.dumps(data, ensure_ascii=False)
    return r.text


def build_gather_record(
    *,
    phase: str,
    decision_date: str,
    rss_items: list[dict[str, Any]],
    url_snippet: str,
    max_items_per_topic: int,
) -> dict[str, Any]:
    digest = build_news_digest(rss_items, max_items_per_topic=max_items_per_topic)
    return {
        "phase": phase,
        "decision_date": decision_date,
        "rss_headlines": rss_items,
        "news_digest": digest,
        "gather_url_summary": url_snippet.strip() if url_snippet else "",
        "note": "已按主题聚合并做情绪粗评分；历史回测场景可能与决策日不完全对齐。",
    }


