from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from invest_system.assistant.constants import INTRADAY_PHASES
from invest_system.assistant.gather import (
    build_gather_record,
    enrich_rss_with_body_excerpts,
    fetch_assistant_gather_url,
    gather_all_rss,
)
from invest_system.assistant.market_slice import build_phase_market_notes
from invest_system.config import Settings
from invest_system.llm_strategy import deepseek_chat_completion_sync
from invest_system.portfolio import Portfolio


BLOGGER_SYSTEM = """你是一名面向 A 股短线交易者的财经博主 + 看盘助手。
目标：输出“主线-证据-风险”结构化短文，强调信息组织与证据引用，不喊单。

强制格式（必须按此顺序）：
1) 标题（1 行）
2) 主线判断（2-4 条）
3) 证据与验证（按主题分组）
4) 风险与反证（2-4 条）
5) 观察清单（3-5 条）
6) 文末固定一行：模拟盘笔记，非投资建议。

证据规则（必须遵守）：
- 仅使用输入中的“证据池(EID)”与盘面摘要，不得编造新闻或数据。
- 每个关键结论后至少引用 1 个 EID（如 [E03]）。
- 每个章节（主线判断/证据与验证/风险与反证）至少引用 2 个不同 source_domain 的证据。
- 若证据不足，明确写“证据不足”，不要脑补。
- 禁止输出 JSON；禁止代码块；禁止精确买卖价位与喊单。"""


def _fallback_article(
    phase_cn: str,
    decision_date: str,
    gather: dict[str, Any],
    market_notes: str,
) -> str:
    lines = [
        f"# {phase_cn} · {decision_date}",
        "",
        "## 资讯提要",
    ]
    rss = gather.get("rss_headlines") or []
    if not rss:
        lines.append("- （未配置 RSS 或抓取失败）")
    else:
        for it in rss[:12]:
            t = it.get("title", "")
            lines.append(f"- {t}")
    extra = (gather.get("gather_url_summary") or "").strip()
    if extra:
        lines.extend(["", "## 聚合摘要（自定义接口）", extra])
    lines.extend(["", "## 盘面（引擎近似）", market_notes, "", "模拟盘笔记，非投资建议。"])
    return "\n".join(lines)


def _compose_article_user_prompt(
    phase_cn: str,
    decision_date: str,
    gather: dict[str, Any],
    market_notes: str,
    cash: float,
    positions: dict[str, float],
    equity: float,
) -> str:
    headlines = gather.get("rss_headlines") or []
    digest = gather.get("news_digest") if isinstance(gather.get("news_digest"), dict) else {}
    evidence_lines = []
    source_count: dict[str, int] = {}
    for i, h in enumerate(headlines[:25], start=1):
        title = str(h.get("title", "")).strip()
        pub = str(h.get("published", "")).strip()
        src = str(h.get("source_feed", "")).strip()
        if not title:
            continue
        eid = f"E{i:02d}"
        domain = ""
        if src:
            try:
                domain = urlparse(src).netloc
            except Exception:
                domain = ""
        domain = domain or "unknown"
        source_count[domain] = source_count.get(domain, 0) + 1
        evidence_lines.append(f"[{eid}] {title} | {pub} | source_domain={domain} | {src}")
    evidence_pool = "\n".join(evidence_lines) if evidence_lines else "（无）"
    source_mix = ", ".join(f"{k}:{v}" for k, v in sorted(source_count.items(), key=lambda x: x[0])) or "（无）"

    head_txt = "\n".join(
        f"- {h.get('title', '')}" for h in headlines[:15] if h.get("title")
    )
    def _topic_lines(topic: str, title: str) -> str:
        arr = digest.get("topic_headlines", {}).get(topic, []) if isinstance(digest, dict) else []
        if not arr:
            return f"{title}: （无）"
        return title + ":\n" + "\n".join(f"- {x.get('title', '')}" for x in arr[:5])

    structured_news = "\n\n".join(
        [
            f"总体情绪: {digest.get('sentiment', 'unknown')} (score={digest.get('sentiment_score', 0)})",
            _topic_lines("macro_policy", "宏观政策"),
            _topic_lines("market_flow", "市场资金与指数"),
            _topic_lines("tech_compute", "科技/算力主线"),
            _topic_lines("company_fundamental", "公司与业绩"),
        ]
    )
    extra = (gather.get("gather_url_summary") or "").strip()
    excerpt_chunks: list[str] = []
    for i, h in enumerate(headlines[:25], start=1):
        ex = str(h.get("body_excerpt", "") or "").strip()
        if ex:
            excerpt_chunks.append(f"[E{i:02d}] 正文摘录（网页抓取，可能被付费墙/反爬截断）:\n{ex}")
    excerpt_block = (
        "\n\n".join(excerpt_chunks)
        if excerpt_chunks
        else "（未抓取正文或摘录为空；付费墙与部分站点会返回空文本）"
    )
    return "\n".join(
        [
            f"节点：{phase_cn}",
            f"决策日：{decision_date}",
            f"账户快照：现金={cash:.2f} 权益≈{equity:.2f} 持仓股数={json.dumps(positions, ensure_ascii=False)}",
            "",
            "【结构化资讯摘要（主题+情绪）】",
            structured_news or "（无）",
            "",
            "【原始标题（摘录）】",
            head_txt or "（无）",
            "",
            "【证据池（引用格式：[EID]）】",
            evidence_pool,
            "",
            "【正文摘录（优先阅读，用于理解语境）】",
            excerpt_block,
            "",
            "【证据来源分布（用于多源交叉验证）】",
            source_mix,
            "",
            "【自定义聚合摘要】",
            extra or "（无）",
            "",
            "【盘面引擎摘要（日线近似）】",
            market_notes,
            "",
            "请按“主线判断-证据与验证-风险与反证-观察清单”输出 450~800 字，"
            "每条关键结论都要尽量引用 [EID]；若缺少证据请明确写“证据不足”。",
        ]
    )


def _build_evidence_index_markdown(article_text: str, gather: dict[str, Any], *, max_items: int = 25) -> str:
    """Append EID -> title/link mapping so readers can click-through."""
    headlines = gather.get("rss_headlines") or []
    if not isinstance(headlines, list) or not headlines:
        return ""
    ref_ids = set(re.findall(r"\[E(\d{2})\]", article_text))
    lines: list[str] = ["## 证据索引（可点击原文）"]
    for i, h in enumerate(headlines[:max_items], start=1):
        eid_num = f"{i:02d}"
        if ref_ids and eid_num not in ref_ids:
            continue
        eid = f"E{eid_num}"
        title = str(h.get("title", "")).strip() or "(无标题)"
        link = str(h.get("link", "")).strip()
        pub = str(h.get("published", "")).strip()
        src = str(h.get("source_feed", "")).strip()
        if link:
            lines.append(f"- [{eid}] [{title}]({link})")
        else:
            lines.append(f"- [{eid}] {title}")
        meta = " | ".join([x for x in [pub, src] if x])
        if meta:
            lines.append(f"  - {meta}")
        ex = str(h.get("body_excerpt", "") or "").strip()
        if ex:
            preview = ex.replace("\r\n", "\n")
            if len(preview) > 1200:
                preview = preview[:1200].rsplit("\n", 1)[0] + "\n…"
            for ln in preview.split("\n"):
                if ln.strip():
                    lines.append(f"  > {ln.strip()}")
    return "\n".join(lines) if len(lines) > 1 else ""


def run_single_intraday_phase(
    settings: Settings,
    *,
    phase_key: str,
    phase_cn: str,
    panel: pd.DataFrame,
    ts: pd.Timestamp,
    decision_day: date,
    watchlist: list[str],
    portfolio: Portfolio,
    prices: dict[str, float],
    benchmarks: list[str],
    day_dir: Path,
    live_execution_prices: dict[str, float] | None = None,
) -> tuple[str, bool]:
    """单个节点：RSS → 短文 → 落盘。返回 (正文 Markdown, 是否使用了模板 fallback)。"""
    cash = portfolio.cash
    positions = dict(portfolio.positions)
    equity = portfolio.equity(prices)

    rss_items = gather_all_rss(settings, symbols=watchlist)
    rss_items = enrich_rss_with_body_excerpts(list(rss_items), settings)
    market_notes = build_phase_market_notes(
        phase_key,
        panel,
        ts,
        watchlist,
        live_execution_prices=live_execution_prices,
    )
    url_snip = fetch_assistant_gather_url(
        settings,
        phase=phase_key,
        decision_date=str(decision_day),
        symbols=watchlist,
        positions=positions,
        cash=cash,
        equity=equity,
        benchmarks=benchmarks,
        market_notes=market_notes,
    )
    gather_rec = build_gather_record(
        phase=phase_key,
        decision_date=str(decision_day),
        rss_items=list(rss_items),
        url_snippet=url_snip,
        max_items_per_topic=int(settings.assistant_max_items_per_topic),
    )

    gpath = day_dir / f"{phase_key}_gather.json"
    gpath.write_text(
        json.dumps(gather_rec, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    user_art = _compose_article_user_prompt(
        phase_cn,
        str(decision_day),
        gather_rec,
        market_notes,
        cash,
        positions,
        equity,
    )
    article_text = deepseek_chat_completion_sync(
        settings,
        system_prompt=BLOGGER_SYSTEM,
        user_prompt=user_art,
        model=settings.assistant_llm_model(),
        temperature=float(settings.assistant_temperature),
        timeout=float(settings.assistant_llm_timeout_sec),
    )
    used_fallback = not article_text.strip()
    if used_fallback:
        article_text = _fallback_article(phase_cn, str(decision_day), gather_rec, market_notes)
    evidence_index = _build_evidence_index_markdown(article_text, gather_rec)
    if evidence_index:
        article_text = article_text.rstrip() + "\n\n" + evidence_index

    apath = day_dir / f"{phase_key}_article.md"
    apath.write_text(article_text.strip() + "\n", encoding="utf-8")
    return article_text.strip(), used_fallback


def run_intraday_assistant_for_day(
    settings: Settings,
    *,
    panel: pd.DataFrame,
    ts: pd.Timestamp,
    decision_day: date,
    watchlist: list[str],
    portfolio: Portfolio,
    prices: dict[str, float],
    benchmarks: list[str],
) -> tuple[str, Path]:
    """
    运行四节点：搜集 → 短文 → 落盘。
    返回 (合并 Markdown 供 LLM 调仓引用, 当日目录)。
    """
    root = Path(settings.assistant_artifacts_dir)
    day_dir = root / str(decision_day)
    day_dir.mkdir(parents=True, exist_ok=True)

    prices_for_equity = prices

    bundle_sections: list[str] = []
    max_chars = int(settings.assistant_max_bundle_chars)
    fallback_nodes = 0

    for phase_key, phase_cn in INTRADAY_PHASES:
        article_text, used_fb = run_single_intraday_phase(
            settings,
            phase_key=phase_key,
            phase_cn=phase_cn,
            panel=panel,
            ts=ts,
            decision_day=decision_day,
            watchlist=watchlist,
            portfolio=portfolio,
            prices=prices_for_equity,
            benchmarks=benchmarks,
            day_dir=day_dir,
            live_execution_prices=None,
        )
        if used_fb:
            fallback_nodes += 1

        bundle_sections.append(f"## {phase_cn} ({phase_key})\n\n{article_text.strip()}\n")

    if fallback_nodes:
        print(
            f"[invest-system] 看盘助手 {fallback_nodes}/{len(INTRADAY_PHASES)} 个节点使用模板博文"
            "（DeepSeek 失败或未配置 KEY）；请检查 ASSISTANT_MODEL 与 KEY。",
            file=sys.stderr,
        )

    full_bundle = "\n---\n\n".join(bundle_sections)
    (day_dir / "day_bundle.md").write_text(full_bundle, encoding="utf-8")

    bundle_for_llm = full_bundle
    if len(bundle_for_llm) > max_chars:
        bundle_for_llm = (
            bundle_for_llm[:max_chars]
            + "\n\n…(看盘助手全文已截断，完整内容见 ASSISTANT_ARTIFACTS_DIR 下当日 markdown)\n"
        )

    return bundle_for_llm, day_dir
