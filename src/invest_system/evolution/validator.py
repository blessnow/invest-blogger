"""Backtest validator: runs simulation with proposed genome and compares metrics."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from invest_system.config import Settings
from invest_system.engine import (
    EquitySnapshot,
    run_simulation,
    set_custom_scoring_fn,
    set_runtime_params,
)
from invest_system.llm_strategy import set_llm_params, set_prompt_overrides
from invest_system.market_scanner import set_scanner_params
from invest_system.evolution.genome import StrategyGenome


@dataclass
class ValidationResult:
    old_metrics: dict[str, float]
    new_metrics: dict[str, float]
    improvement: dict[str, float]
    accepted: bool
    rejection_reason: str | None = None


def _snapshot_metrics(snapshots: list[EquitySnapshot], initial_capital: float) -> dict[str, float]:
    if not snapshots:
        return {"total_return_pct": 0, "sharpe_ratio": 0, "max_drawdown_pct": 0, "win_rate_pct": 0}

    equities = [s.equity for s in snapshots]
    arr = np.array(equities)

    total_return = (arr[-1] / initial_capital - 1) * 100

    if len(arr) > 1:
        daily = np.diff(arr) / arr[:-1]
        sharpe = float(np.mean(daily) / max(np.std(daily), 1e-9) * np.sqrt(252))
    else:
        sharpe = 0.0

    peak = np.maximum.accumulate(arr)
    drawdown = (arr - peak) / peak * 100
    max_dd = float(np.min(drawdown))

    return {
        "total_return_pct": round(total_return, 4),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(max_dd, 4),
    }


def _apply_genome_to_settings(genome: StrategyGenome, base: Settings) -> Settings:
    s = copy.deepcopy(base)
    for key, value in genome.config_overrides.items():
        if key in ("llm_temperature",):
            continue
        if hasattr(s, key):
            try:
                setattr(s, key, type(getattr(s, key))(value))
            except (TypeError, ValueError):
                pass
    return s


def _apply_genome_runtime(genome: StrategyGenome) -> dict[str, Any]:
    """Apply genome to runtime params. Returns previous state for restoration."""
    from invest_system.engine import get_runtime_params, get_custom_scoring_fn
    from invest_system.llm_strategy import get_llm_params
    from invest_system.market_scanner import get_scanner_params
    from invest_system.engine_hooks import get_hook_registry

    hooks = get_hook_registry()
    saved = {
        "runtime": get_runtime_params(),
        "llm": get_llm_params(),
        "scanner": get_scanner_params(),
        "custom_fn": get_custom_scoring_fn(),
        "hooks": hooks.snapshot(),
    }

    # Runtime params
    hp = {}
    for k in ("candidate_score_weight_ret1d", "candidate_score_weight_ret5d",
              "recent_bars_lookback", "watchlist_cap", "deploy_fraction"):
        if k in genome.hardcoded_param_overrides:
            hp[k] = genome.hardcoded_param_overrides[k]
    if hp:
        set_runtime_params(hp)

    # Scanner params
    sp = {}
    for k in ("scanner_score_weight_chg", "scanner_score_weight_amt"):
        if k in genome.hardcoded_param_overrides:
            sp[k] = genome.hardcoded_param_overrides[k]
    if sp:
        set_scanner_params(sp)

    # LLM params
    if "llm_temperature" in genome.config_overrides:
        set_llm_params({"llm_temperature": float(genome.config_overrides["llm_temperature"])})

    # Prompt overrides
    if genome.prompt_overrides:
        set_prompt_overrides(genome.prompt_overrides)

    # Custom scoring function (legacy support)
    if genome.code_overrides and genome.code_overrides.get("candidate_scoring_fn"):
        from invest_system.evolution.evolver import load_scoring_function
        try:
            fn = load_scoring_function(genome.code_overrides["candidate_scoring_fn"])
            set_custom_scoring_fn(fn)
        except Exception:
            set_custom_scoring_fn(None)
    else:
        set_custom_scoring_fn(None)

    # Strategy hooks
    _load_hooks_from_genome(genome, hooks)

    return saved


def _load_hooks_from_genome(genome: StrategyGenome, hooks: Any) -> None:
    """Load all strategy hook functions from genome code_overrides into the registry."""
    from invest_system.evolution.evolver import load_hook_function, _HOOK_EXPECTED_TYPES

    hook_keys = {
        "should_rebalance_fn": "should_rebalance",
        "candidate_scoring_fn": "score_candidate",
        "size_position_fn": "size_position",
        "check_exit_fn": "check_exit",
        "filter_risk_fn": "filter_risk",
    }
    for code_key, fn_name in hook_keys.items():
        src = genome.code_overrides.get(code_key, "")
        if src and src.strip():
            try:
                expected = _HOOK_EXPECTED_TYPES.get(fn_name)
                fn = load_hook_function(src, fn_name, expected)
                hooks.set_hook(fn_name, fn)
            except Exception:
                hooks.set_hook(fn_name, None)
        else:
            hooks.set_hook(fn_name, None)


def _restore_runtime(saved: dict[str, Any]) -> None:
    set_runtime_params(saved["runtime"])
    set_scanner_params(saved["scanner"])
    set_llm_params(saved["llm"])
    set_custom_scoring_fn(saved["custom_fn"])
    if "hooks" in saved:
        from invest_system.engine_hooks import get_hook_registry
        get_hook_registry().restore(saved["hooks"])


class BacktestValidator:
    def __init__(self, settings: Settings, price_df: pd.DataFrame):
        self.base_settings = settings
        self.price_df = price_df

    def validate(
        self,
        proposed: StrategyGenome,
        baseline: StrategyGenome | None = None,
    ) -> ValidationResult:
        initial_capital = float(self.base_settings.initial_capital)

        # Run baseline backtest (with current active genome or defaults)
        baseline_settings = self.base_settings
        if baseline:
            baseline_settings = _apply_genome_to_settings(baseline, self.base_settings)

        saved_state = _apply_genome_runtime(baseline if baseline else StrategyGenome())
        try:
            _, base_snapshots, _ = run_simulation(baseline_settings, self.price_df)
        except Exception as e:
            _restore_runtime(saved_state)
            return ValidationResult(
                old_metrics={},
                new_metrics={},
                improvement={},
                accepted=False,
                rejection_reason=f"Baseline backtest failed: {e}",
            )
        finally:
            _restore_runtime(saved_state)

        old_metrics = _snapshot_metrics(base_snapshots, initial_capital)

        # Run proposed backtest
        proposed_settings = _apply_genome_to_settings(proposed, self.base_settings)
        saved_proposed = _apply_genome_runtime(proposed)
        try:
            _, new_snapshots, _ = run_simulation(proposed_settings, self.price_df)
        except Exception as e:
            _restore_runtime(saved_proposed)
            return ValidationResult(
                old_metrics=old_metrics,
                new_metrics={},
                improvement={},
                accepted=False,
                rejection_reason=f"Proposed backtest failed: {e}",
            )
        finally:
            _restore_runtime(saved_proposed)

        new_metrics = _snapshot_metrics(new_snapshots, initial_capital)

        # Compute improvements
        improvement = {}
        for k in old_metrics:
            if k in new_metrics:
                improvement[k] = round(new_metrics[k] - old_metrics[k], 4)

        # Evaluate acceptance
        accepted, reason = self._evaluate(old_metrics, new_metrics)
        return ValidationResult(
            old_metrics=old_metrics,
            new_metrics=new_metrics,
            improvement=improvement,
            accepted=accepted,
            rejection_reason=reason,
        )

    def _evaluate(self, old: dict, new: dict) -> tuple[bool, str | None]:
        # Must have at least total return
        if "total_return_pct" not in new:
            return False, "No metrics from proposed genome"

        # 1. Return not severely degraded
        if new["total_return_pct"] < old.get("total_return_pct", 0) - 2.0:
            return False, (
                f"Return degraded too much: {new['total_return_pct']:.2f}% vs "
                f"{old.get('total_return_pct', 0):.2f}%"
            )

        # 2. Sharpe not severely degraded
        if new.get("sharpe_ratio", 0) < old.get("sharpe_ratio", 0) - 0.2:
            return False, (
                f"Sharpe degraded too much: {new.get('sharpe_ratio', 0):.3f} vs "
                f"{old.get('sharpe_ratio', 0):.3f}"
            )

        # 3. Drawdown not much worse
        if new.get("max_drawdown_pct", 0) < old.get("max_drawdown_pct", 0) - 5.0:
            return False, (
                f"Drawdown worsened too much: {new.get('max_drawdown_pct', 0):.2f}% vs "
                f"{old.get('max_drawdown_pct', 0):.2f}%"
            )

        # 4. Composite score positive
        eps = 1e-6
        delta_ret = new.get("total_return_pct", 0) - old.get("total_return_pct", 0)
        delta_sharpe = new.get("sharpe_ratio", 0) - old.get("sharpe_ratio", 0)
        delta_dd = new.get("max_drawdown_pct", 0) - old.get("max_drawdown_pct", 0)  # less negative = better

        norm_ret = delta_ret / max(abs(old.get("total_return_pct", eps)), eps)
        norm_sharpe = delta_sharpe / max(abs(old.get("sharpe_ratio", eps)), eps)
        norm_dd = delta_dd / max(abs(old.get("max_drawdown_pct", eps)), eps)

        composite = 0.4 * norm_ret + 0.3 * norm_sharpe + 0.3 * norm_dd
        if composite <= 0:
            return False, f"Composite score not positive: {composite:.4f}"

        return True, None
