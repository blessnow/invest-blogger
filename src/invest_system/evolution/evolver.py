"""Strategy evolver: uses LLM to analyze performance and propose genome mutations."""

from __future__ import annotations

import json
from typing import Any, Callable

from invest_system.config import Settings
from invest_system.llm_strategy import deepseek_decision_sync, _extract_json
from invest_system.evolution.genome import StrategyGenome
from invest_system.evolution.analyzer import AnalysisReport


# ---------------------------------------------------------------------------
# Code sandbox for generated strategy hook functions
# ---------------------------------------------------------------------------
_ALLOWED_BUILTINS = {
    "abs": abs, "min": min, "max": max, "len": len,
    "float": float, "int": int, "round": round,
    "pow": pow, "sum": sum, "sorted": sorted,
    "list": list, "dict": dict, "range": range,
    "enumerate": enumerate, "zip": zip,
    "isinstance": isinstance, "str": str, "bool": bool,
    "True": True, "False": False, "None": None,
}

# Sample inputs for validating each hook type
_HOOK_SAMPLES = {
    "score_candidate": {"symbol": "600519.SS", "ret1d_pct": 1.0, "ret5d_pct": 2.0,
                        "ret10d_pct": 3.0, "ret20d_pct": 4.0, "last_close": 50.0,
                        "avg_volume_5d": 1e6, "notional": 5e7, "volatility_10d": 1.5,
                        "high_10d": 55.0, "low_10d": 45.0},
    "should_rebalance": {"day_index": 5, "day": "2024-03-15",
                         "days_since_rebalance": 1, "default_interval": 1,
                         "portfolio": {"cash": 50000, "equity": 100000,
                                       "positions": {}, "num_positions": 0},
                         "prices": {}, "recent_returns": [0.5, -0.3],
                         "drawdown_pct": -2.0},
    "size_position": {"symbol": "600519.SS", "side": "buy", "requested_shares": 100,
                      "price": 50.0, "current_shares": 0, "current_market_value": 0,
                      "position_fraction": 0.0, "max_fraction": 0.3,
                      "avg_cost": None, "unrealized_pnl_pct": None,
                      "recent_bars": [50.0, 51.0], "max_fraction_config": 0.3,
                      "portfolio": {"cash": 50000, "equity": 100000,
                                    "positions": {}, "num_positions": 0}},
    "check_exit": {"day": "2024-03-15", "positions": {"600519.SS": {
                    "shares": 100, "avg_cost": 48.0, "current_price": 50.0,
                    "unrealized_pnl_pct": 4.17, "recent_bars": [48, 49, 50]}},
                   "portfolio": {"cash": 50000, "equity": 100000},
                   "market_regime": "neutral"},
    "filter_risk": {"day": "2024-03-15", "actions": [],
                    "portfolio": {"cash": 50000, "equity": 100000,
                                  "positions": {}, "num_positions": 0},
                    "prices": {}, "max_position_fraction": 0.3,
                    "position_fractions": {}, "drawdown_pct": -2.0,
                    "recent_returns": [0.5]},
}

_HOOK_EXPECTED_TYPES = {
    "score_candidate": float,
    "should_rebalance": bool,
    "size_position": int,
    "check_exit": list,
    "filter_risk": list,
}


def load_scoring_function(source: str, function_name: str = "score_candidate") -> Callable:
    return load_hook_function(source, function_name)


def load_hook_function(source: str, function_name: str, expected_type: type | None = None) -> Callable:
    ns: dict[str, Any] = {"__builtins__": _ALLOWED_BUILTINS}
    exec(source, ns)
    fn = ns.get(function_name)
    if not callable(fn):
        raise ValueError(f"{function_name} not found in source")
    exp = expected_type or _HOOK_EXPECTED_TYPES.get(function_name, (int, float))
    sample = _HOOK_SAMPLES.get(function_name)
    if sample:
        result = fn(sample)
        if exp == float:
            if not isinstance(result, (int, float)):
                raise ValueError(f"{function_name} must return a number")
        elif exp == int:
            if not isinstance(result, (int, float)):
                raise ValueError(f"{function_name} must return an integer")
        elif exp == bool:
            if not isinstance(result, (bool, int)):
                raise ValueError(f"{function_name} must return bool")
        elif exp == list:
            if not isinstance(result, list):
                raise ValueError(f"{function_name} must return a list")
    return fn


# ---------------------------------------------------------------------------
# Evolution prompts
# ---------------------------------------------------------------------------
EVOLUTION_SYSTEM_PROMPT = """你是量化策略进化引擎。你的任务是分析投资组合的历史表现，识别弱点，并提出策略修改建议。

你收到的输入包含：
1. 当前策略基因组（所有可调参数的快照）
2. 性能分析报告（收益率、夏普比率、最大回撤、交易模式分析等）

你需要输出一个 JSON 对象，包含对策略的具体修改建议。

可修改的维度：
- config_overrides: max_position_fraction(0.05-0.50), rebalance_every_days(1-5), market_candidates_top_n(5-50), llm_temperature(0.0-1.0)
- hardcoded_param_overrides: candidate_score_weight_ret1d/ret5d(和=1.0), recent_bars_lookback(5-60), watchlist_cap(10-50), deploy_fraction(0.5-1.0), scanner_score_weight_chg/amt(和=1.0)
- prompt_overrides: system_fixed 或 system_free 完整替换文本。保留核心约束（仅做多、T+1、lots整数倍、JSON输出格式）。

策略代码钩子（code_overrides）——生成 Python 函数替换引擎策略逻辑：
- candidate_scoring_fn: def score_candidate(ctx) -> float
  ctx: symbol, ret1d_pct, ret5d_pct, ret10d_pct, ret20d_pct, last_close, avg_volume_5d, notional, volatility_10d, high_10d, low_10d

- should_rebalance_fn: def should_rebalance(ctx) -> bool
  ctx: day_index, day, days_since_rebalance, portfolio({cash,equity,positions,num_positions}), prices, recent_returns, drawdown_pct, default_interval

- check_exit_fn: def check_exit(ctx) -> list[dict]
  ctx: day, positions({symbol:{shares,avg_cost,current_price,unrealized_pnl_pct,recent_bars}}), portfolio({cash,equity}), market_regime("bull"/"bear"/"neutral")
  返回 [{"symbol":"...", "reason":"stop_loss"}] 强制平仓

- size_position_fn: def size_position(ctx) -> int
  ctx: symbol, side, requested_shares, price, portfolio, current_shares, avg_cost, unrealized_pnl_pct, recent_bars, max_fraction_config

- filter_risk_fn: def filter_risk(ctx) -> list[dict]
  ctx: day, actions, portfolio, prices, max_position_fraction, position_fractions, drawdown_pct, recent_returns
  返回过滤后 actions 子集

约束：
- 每次最多修改 3 个维度，避免激进突变
- 参数值必须在允许范围内
- prompt 修改必须保留安全约束
- code 必须是无副作用的纯函数，不 import 任何模块
- 保守优先：小幅调整优于大幅调整

输出格式（严格 JSON，无 Markdown）：
{
  "mutations": {
    "config_overrides": {},
    "hardcoded_param_overrides": {},
    "prompt_overrides": {},
    "code_overrides": {}
  },
  "reasoning": "修改逻辑说明",
  "confidence": 0.7
}"""


def _build_evolution_user_prompt(
    genome: StrategyGenome,
    report: AnalysisReport,
    recent_history: list[dict],
) -> str:
    genome_json = json.dumps(
        {
            "config_overrides": genome.config_overrides,
            "hardcoded_param_overrides": genome.hardcoded_param_overrides,
            "prompt_overrides": {k: v[:100] + "..." for k, v in genome.prompt_overrides.items()} if genome.prompt_overrides else {},
            "code_overrides": {k: v[:100] + "..." for k, v in genome.code_overrides.items()} if genome.code_overrides else {},
        },
        ensure_ascii=False,
        indent=2,
    )

    report_json = json.dumps(
        {
            "total_return_pct": report.total_return_pct,
            "sharpe_ratio": report.sharpe_ratio,
            "max_drawdown_pct": report.max_drawdown_pct,
            "win_rate_pct": report.win_rate_pct,
            "profit_factor": report.profit_factor,
            "total_trades": report.total_trades,
            "avg_holding_period_days": report.avg_holding_period_days,
            "turnover_rate": report.turnover_rate,
            "per_symbol_pnl": report.per_symbol_pnl,
            "losing_patterns": report.losing_patterns,
            "winning_patterns": report.winning_patterns,
            "weakness_signals": report.weakness_signals,
            "suggested_focus_areas": report.suggested_focus_areas,
        },
        ensure_ascii=False,
        indent=2,
    )

    history_text = ""
    if recent_history:
        history_text = "\n## 最近进化历史（避免重复方向）\n"
        for h in recent_history[-5:]:
            history_text += f"- gen{h.get('generation', '?')}: {h.get('mutation_summary', 'N/A')}\n"

    return f"""## 当前策略基因组
{genome_json}

## 性能分析报告
{report_json}
{history_text}
请分析当前策略的弱点并提出改进建议。输出严格遵循 JSON 格式。"""


def propose_mutation(
    settings: Settings,
    current_genome: StrategyGenome,
    analysis_report: AnalysisReport,
    recent_history: list[dict] | None = None,
) -> StrategyGenome:
    """Use LLM to propose a genome mutation. Returns a new child genome."""
    user_prompt = _build_evolution_user_prompt(
        current_genome, analysis_report, recent_history or []
    )

    try:
        response = deepseek_decision_sync(
            settings,
            system_prompt=EVOLUTION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            timeout=120.0,
        )
    except Exception as e:
        raise RuntimeError(f"Evolution LLM call failed: {e}") from e

    mutations = response.get("mutations", {})
    if not isinstance(mutations, dict):
        mutations = {}

    child = current_genome.child(mutations)
    child.metadata.evolution_reason = response.get("reasoning", "")
    child.metadata.mutation_summary = _summarize_mutations(mutations)
    child.metadata.analysis_report_id = analysis_report.report_id

    # Validate sanity of mutations
    _validate_genome_sanity(child)

    return child


def _summarize_mutations(mutations: dict) -> str:
    parts = []
    for section, values in mutations.items():
        if isinstance(values, dict) and values:
            keys = ", ".join(values.keys())
            parts.append(f"{section}: [{keys}]")
    return "; ".join(parts) if parts else "no changes"


def _validate_genome_sanity(genome: StrategyGenome) -> None:
    co = genome.config_overrides
    if "max_position_fraction" in co:
        co["max_position_fraction"] = max(0.05, min(0.50, float(co["max_position_fraction"])))
    if "rebalance_every_days" in co:
        co["rebalance_every_days"] = max(1, min(5, int(co["rebalance_every_days"])))
    if "market_candidates_top_n" in co:
        co["market_candidates_top_n"] = max(5, min(50, int(co["market_candidates_top_n"])))
    if "llm_temperature" in co:
        co["llm_temperature"] = max(0.0, min(1.0, float(co["llm_temperature"])))

    hp = genome.hardcoded_param_overrides
    if "recent_bars_lookback" in hp:
        hp["recent_bars_lookback"] = max(5, min(60, int(hp["recent_bars_lookback"])))
    if "watchlist_cap" in hp:
        hp["watchlist_cap"] = max(10, min(50, int(hp["watchlist_cap"])))
    if "deploy_fraction" in hp:
        hp["deploy_fraction"] = max(0.5, min(1.0, float(hp["deploy_fraction"])))

    # Validate code overrides (all hooks)
    for hook_key in list(genome.code_overrides.keys()):
        src = genome.code_overrides.get(hook_key, "")
        if not src or not src.strip():
            continue
        fn_name = hook_key.replace("_fn", "").replace("candidate_scoring", "score_candidate")
        expected = _HOOK_EXPECTED_TYPES.get(fn_name)
        try:
            load_hook_function(src, fn_name, expected)
        except Exception:
            genome.code_overrides.pop(hook_key, None)


def run_evolution_cycle(settings: Settings) -> StrategyGenome | None:
    """Run a full evolution cycle: analyze → propose → validate → apply.

    Returns the new genome if accepted, None otherwise.
    """
    from invest_system.evolution.analyzer import PerformanceAnalyzer
    from invest_system.evolution.validator import BacktestValidator
    from invest_system.evolution.applier import GenomeApplier
    from invest_system.data_feed import download_prices

    genome_dir = settings.evolution_genome_dir

    # 1. Load or create current genome
    current = StrategyGenome.load_active(genome_dir)
    if current is None:
        current = StrategyGenome()

    # 2. Analyze performance
    equity_csv = settings.data_dir / f"{settings.live_equity_csv_prefix}_equity.csv"
    tx_csv = settings.data_dir / f"{settings.live_equity_csv_prefix}_transactions.csv"

    analyzer = PerformanceAnalyzer(
        equity_csv=equity_csv,
        transactions_csv=tx_csv,
        initial_capital=float(settings.initial_capital),
    )
    report = analyzer.analyze()

    # Save report
    reports_dir = genome_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / f"{report.report_id}.json").write_text(
        report.to_json(), encoding="utf-8"
    )

    print(f"[evolution] Analysis report: return={report.total_return_pct:.2f}%, "
          f"sharpe={report.sharpe_ratio:.3f}, dd={report.max_drawdown_pct:.2f}%")

    # 3. Propose mutation
    history = StrategyGenome.load_history(genome_dir)
    try:
        proposed = propose_mutation(settings, current, report, recent_history=history)
    except Exception as e:
        print(f"[evolution] Mutation proposal failed: {e}")
        return None

    print(f"[evolution] Proposed mutation: {proposed.metadata.mutation_summary}")

    # 4. Validate via backtest
    start = settings.evolution_backtest_start
    end = settings.evolution_backtest_end
    symbols = settings.symbols() + settings.reference_benchmark_symbols()
    try:
        price_df = download_prices(symbols, start, end, settings.data_dir)
    except Exception as e:
        print(f"[evolution] Price data download failed: {e}")
        return None

    validator = BacktestValidator(settings, price_df)
    result = validator.validate(proposed, baseline=current)

    proposed.validation.total_return_pct = result.new_metrics.get("total_return_pct", 0)
    proposed.validation.sharpe_ratio = result.new_metrics.get("sharpe_ratio", 0)
    proposed.validation.max_drawdown_pct = result.new_metrics.get("max_drawdown_pct", 0)
    proposed.validation.backtest_start = start
    proposed.validation.backtest_end = end
    proposed.validation.total_trades = report.total_trades

    if result.accepted:
        proposed.validation.accepted = True
        from datetime import datetime, timezone
        proposed.validation.accepted_at = datetime.now(timezone.utc).isoformat()
        print(f"[evolution] ACCEPTED: return={result.new_metrics.get('total_return_pct', 0):.2f}% "
              f"(was {result.old_metrics.get('total_return_pct', 0):.2f}%)")
    else:
        proposed.validation.accepted = False
        print(f"[evolution] REJECTED: {result.rejection_reason}")

    # 5. Save genome (accepted or not)
    proposed.save(genome_dir)
    proposed.append_history(genome_dir)

    # 6. Apply if accepted
    if result.accepted:
        applier = GenomeApplier()
        applier.apply(proposed, settings)
        proposed.save_as_active(genome_dir)
        return proposed

    return None
