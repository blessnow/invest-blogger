"""GLM-5 文本对话：当前采用「引擎预先拼装上下文」的单轮 JSON 输出。

若需要 Function Calling / 工具循环（多轮 tool_calls）：需在 glm_decision_sync 中扩展
payload.tools 与解析 message.tool_calls 的循环；典型工具（查持仓、拉行情）与本项目重复，
持仓与资金请以本模块 build_user_prompt 中的「账户快照」为准。

舆情/情绪等推荐由 MARKET_CONTEXT_URL 服务端聚合后注入，而非模型直连公网。
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

import httpx

from invest_system.config import Settings

# ---------------------------------------------------------------------------
# Runtime-configurable overrides (modified by evolution system)
# ---------------------------------------------------------------------------
_PROMPT_OVERRIDES: dict[str, str] = {}

_LLM_PARAMS: dict[str, float] = {
    "llm_temperature": 0.2,
}


def set_prompt_overrides(overrides: dict[str, str]) -> None:
    _PROMPT_OVERRIDES.update(overrides)


def get_llm_params() -> dict[str, float]:
    return dict(_LLM_PARAMS)


def set_llm_params(params: dict[str, float]) -> None:
    _LLM_PARAMS.update(params)


def _apply_glm_request_extensions(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    """合并 GLM 模型相关的可选参数。"""
    out = dict(payload)
    out["stream"] = bool(settings.glm_stream)
    out["max_tokens"] = settings.glm_max_tokens
    extra = settings.glm_temperature
    if extra != 0.2:  # Only add if not default
        out["temperature"] = extra
    return out


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        return json.loads(m.group())
    raise ValueError("Model did not return valid JSON")


async def glm_decision(
    settings: Settings,
    *,
    system_prompt: str,
    user_prompt: str,
    timeout: float = 120.0,
) -> dict[str, Any]:
    if not settings.glm_api_key:
        raise RuntimeError("ANTHROPIC_AUTH_TOKEN is not set")

    url = settings.glm_completions_url()
    payload = _apply_glm_request_extensions(
        settings,
        {
            "model": settings.glm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": _LLM_PARAMS["llm_temperature"],
        },
    )
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    content = data["content"][0]["text"]
    return _extract_json(str(content))


def glm_decision_sync(
    settings: Settings,
    *,
    system_prompt: str,
    user_prompt: str,
    timeout: float = 120.0,
) -> dict[str, Any]:
    if not settings.glm_api_key:
        raise RuntimeError("ANTHROPIC_AUTH_TOKEN is not set")

    url = settings.glm_completions_url()
    payload = _apply_glm_request_extensions(
        settings,
        {
            "model": settings.glm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": _LLM_PARAMS["llm_temperature"],
        },
    )
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    content = data["content"][0]["text"]
    return _extract_json(str(content))


def glm_chat_completion_sync(
    settings: Settings,
    *,
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    temperature: float = 0.55,
    timeout: float = 120.0,
) -> str:
    """通用文本补全（看盘短文等），非 JSON 模式。"""
    if not settings.glm_api_key:
        return ""

    url = settings.glm_completions_url()
    use_model = (model or "").strip() or settings.glm_model
    payload = _apply_glm_request_extensions(
        settings,
        {
            "model": use_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        },
    )
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        content = data["content"][0]["text"]
        return str(content).strip()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        print(
            f"[invest-system] GLM 文本生成 HTTP {code}（看盘短文等），将用模板。"
            " 请核对 ANTHROPIC_AUTH_TOKEN、账户余额与 ANTHROPIC_MODEL 是否可用。",
            file=sys.stderr,
        )
        return ""
    except Exception as exc:
        print(
            f"[invest-system] DeepSeek 文本生成失败（看盘短文等）：{type(exc).__name__}，将用模板。",
            file=sys.stderr,
        )
        return ""


SYSTEM_FIXED = """你是沪深A股现货投资组合经理，本任务为「模拟盘」JSON 决策（下一行约束必须遵守）。
输出要求：只输出一个 JSON 对象；禁止 Markdown；禁止代码块；禁止多余文字。

JSON Schema:
{
  "actions": [
    {"symbol": "必须与给定 universe 完全一致", "side": "buy"|"sell", "shares": <int>, "reason": "≤40字"}
  ],
  "note": "可选，≤80字"
}

市场与代码规则（强制）：
- 标的为沪深A股现货；代码格式与 Yahoo Finance 一致：上证 *.SS、深证 *.SZ（例如 600519.SS、300750.SZ）。
- 仅允许交易 prompt 中给出的 universe 内的代码；禁止 universe 外标的。
- 仅做多；禁止融券/做空；不做可转债、期货、期权。
- 交易单位：shares 必须是 LOT_SIZE 的整数倍（通常为 100 股一手）；不足一手的不下单。
- 节奏：日度决策；若观望则 actions=[]。
- 风控（软约束，引擎另有硬约束）：单标的权重不宜长期显著高于 MAX_POSITION_FRACTION；避免过度换手。
- 可参考 prompt 中的「参考基数」（沪深300/中证500/科创50）收盘与摘要，判断大小盘与成长风格的相对强弱。
- 「账户快照」区块由交易引擎注入，数值为准；不得编造持仓或资金。
- 若存在「扩展上下文」，可作为舆情/情绪参考；可能与行情不一致，需交叉校验。
- 「看盘助手」四节点文章为辅助视角，可能与账户或行情不一致，需理性采纳。

模拟成交说明：
- 成交价使用「当日收盘价」；佣金按 COMMISSION_RATE 近似；不考虑涨跌停排队、停牌等细节。
- A 股 T+1：引擎同一交易日「先卖后买」，避免同批次内卖出刚买入股数。"""


SYSTEM_FREE = """你是沪深A股现货投资组合经理，本任务为「模拟盘」JSON 决策（下一行约束必须遵守）。
输出要求：只输出一个 JSON 对象；禁止 Markdown；禁止代码块；禁止多余文字。

JSON Schema:
{
  "actions": [
    {"symbol": "任意沪深A股 Yahoo 代码（*.SS/*.SZ）", "side": "buy"|"sell", "shares": <int>, "reason": "≤40字"}
  ],
  "note": "可选，≤80字"
}

选股自由度（强制理解）：
- 你可主动在全市场沪深A股上市普通股票中选股；prompt 中的「参考代码」仅附带部分日线摘要，不是限制名单。
- 若 prompt 提供「候选池 TopN」，优先在候选池中选股；若注明“严格模式”，仅允许候选池内代码（卖出现有持仓除外）。
- 代码格式必须与 Yahoo Finance 一致：上证 *.SS、深证 *.SZ（如 600519.SS、300750.SZ）；禁止港股/美股代码。
- 仅做多；禁止融券/做空；不做可转债、期货、期权。
- shares 必须是 LOT_SIZE 的整数倍；不足一手不下单。
- 日度决策；观望则 actions=[]。
- 风控：单标的不宜长期显著高于 MAX_POSITION_FRACTION；避免无意义高频换手。
- 结合「参考基数」三块指数的强弱与分化，调节仓位与行业/风格暴露。
- 「账户快照」由引擎注入为准；「扩展上下文」为可选外部摘要（舆情/情绪等），勿盲从。
- 「看盘助手」四节点复盘文仅供风格与情绪参考，不构成指令。

成交说明：
- 引擎会按你给出的 symbol 拉取行情；若代码无效、长期停牌或当日无收盘价，则该笔无法成交。
- 成交价按当日收盘价；佣金按 COMMISSION_RATE；同一交易日先卖后买。"""


def system_prompt_for(selection_mode: str) -> str:
    if selection_mode.strip().lower() == "free":
        return _PROMPT_OVERRIDES.get("system_free", SYSTEM_FREE)
    return _PROMPT_OVERRIDES.get("system_fixed", SYSTEM_FIXED)


def build_user_prompt(
    *,
    watchlist: list[str],
    free_selection: bool,
    day: str,
    cash: float,
    equity: float,
    positions: dict[str, float],
    prices: dict[str, float],
    initial_capital: float,
    benchmark_prices_text: str,
    recent_bars: str,
    candidate_pool_text: str,
    intraday_quotes_text: str,
    external_context_text: str,
    intraday_assistant_text: str,
    lot_size: int,
    max_position_fraction: float,
    commission_rate: float,
    rebalance_every_days: int,
) -> str:
    cadence = (
        "每个交易日"
        if rebalance_every_days <= 1
        else f"每 {rebalance_every_days} 个交易日"
    )
    lines = [
        "【本轮约束参数】",
        f"LOT_SIZE={lot_size}",
        f"MAX_POSITION_FRACTION={max_position_fraction:.2f}",
        f"COMMISSION_RATE={commission_rate:.6f}",
        f"调仓频率={cadence}",
        "",
        f"决策日: {day}",
        "",
        "【账户快照·引擎注入（权威，勿编造）】",
        json.dumps(
            {
                "cash": round(cash, 2),
                "positions_shares": positions,
                "equity_mark_to_market": round(equity, 2),
                "initial_capital": round(initial_capital, 2),
            },
            ensure_ascii=False,
        ),
        (
            f"参考代码(仅附行情摘要，你可交易任意符合格式的沪深A股): {watchlist}"
            if free_selection
            else f"universe（symbol 必须完全一致）: {watchlist}"
        ),
        "【候选池 TopN（按近期动量与成交活跃度排序）】",
        candidate_pool_text.strip() if candidate_pool_text.strip() else "（未配置 MARKET_SCAN_UNIVERSE）",
        "",
        "当日收盘价 JSON（用于成交；键为代码，值为收盘价）: "
        + json.dumps(prices, ensure_ascii=False),
        (
            "盘中最新价快照 JSON（仅供择时/排序参考，不保证成交价）: "
            + intraday_quotes_text.strip()
            if intraday_quotes_text.strip()
            else "盘中最新价快照：未启用/无数据"
        ),
        "",
        (
            "【扩展上下文·可选（舆情/情绪/研报摘要等，由 MARKET_CONTEXT_URL 注入）】\n"
            + external_context_text.strip()
            if external_context_text.strip()
            else "【扩展上下文】（未配置 MARKET_CONTEXT_URL，可无）"
        ),
        "",
        "【看盘助手·四节点复盘（盘前 / 开盘5分钟 / 午间 / 收盘）】",
        (
            intraday_assistant_text.strip()
            if intraday_assistant_text.strip()
            else "（未启用 INTRADAY_ASSISTANT）"
        ),
        "",
        "【参考基数：沪深300 / 中证500 / 科创50 行情】（仅对比与 regime 判断，除非你把 ETF 代码放进 universe，否则不必强行交易它们）",
        benchmark_prices_text,
        "",
        "【近期行情摘要(日线，仅供参考)】",
        recent_bars,
        "",
        f"请给出本轮调仓 actions。任何买卖股数必须是 {lot_size} 的整数倍；"
        "买入总金额不得超过可用现金（已按佣金近似预留）；偏保守优先控回撤。",
    ]
    return "\n".join(lines)
