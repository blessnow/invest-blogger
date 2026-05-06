"""
可选：在调用大模型前注入「市场行情 / 舆情 / 情绪」等扩展上下文。

推荐架构（生产常用）：
- **服务端检索**：由你自己的微服务或 n8n 根据持仓与标的拉取新闻、公告、资金流、情绪打分，
  汇总成短文本；本模块通过 HTTP POST 拉取该摘要并塞进 user prompt。
- **不建议**：把任意浏览器/搜索工具完全交给大模型（成本高、难审计、易幻觉）。

若未配置 MARKET_CONTEXT_URL，本模块返回空字符串，不影响回测。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from invest_system.config import Settings


def fetch_external_context(
    settings: Settings,
    *,
    decision_day: str,
    symbols: list[str],
    positions: dict[str, float],
    cash: float,
    equity: float,
    benchmarks: list[str],
) -> str:
    url = (settings.market_context_url or "").strip()
    if not url:
        return ""

    payload: dict[str, Any] = {
        "decision_date": decision_day,
        "symbols": symbols,
        "positions": positions,
        "cash": cash,
        "equity": equity,
        "benchmarks": benchmarks,
    }
    timeout = float(settings.market_context_timeout_sec)
    max_chars = int(settings.market_context_max_chars)

    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            text = _normalize_response_body(r)
    except Exception:
        return ""

    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…(截断)"
    return text


def _normalize_response_body(r: httpx.Response) -> str:
    ctype = (r.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        data = r.json()
        if isinstance(data, dict):
            for key in ("context", "summary", "text", "markdown"):
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    return v
        return json.dumps(data, ensure_ascii=False)
    return r.text

