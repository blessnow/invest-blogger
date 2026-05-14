"""Genome applier: applies accepted genome to running system."""

from __future__ import annotations

from invest_system.config import Settings
from invest_system.engine import set_runtime_params, set_custom_scoring_fn
from invest_system.llm_strategy import set_prompt_overrides, set_llm_params
from invest_system.market_scanner import set_scanner_params
from invest_system.evolution.genome import StrategyGenome
from invest_system.evolution.evolver import load_scoring_function


class GenomeApplier:
    def apply(self, genome: StrategyGenome, settings: Settings) -> None:
        """Apply an accepted genome to the running system."""
        # Config overrides on settings object
        for key, value in genome.config_overrides.items():
            if key in ("llm_temperature",):
                continue
            if hasattr(settings, key):
                try:
                    setattr(settings, key, type(getattr(settings, key))(value))
                except (TypeError, ValueError):
                    pass

        # LLM temperature
        if "llm_temperature" in genome.config_overrides:
            set_llm_params({"llm_temperature": float(genome.config_overrides["llm_temperature"])})

        # Hardcoded param overrides
        engine_params = {}
        scanner_params = {}
        for k, v in genome.hardcoded_param_overrides.items():
            if k.startswith("scanner_"):
                scanner_params[k] = v
            else:
                engine_params[k] = v
        if engine_params:
            set_runtime_params(engine_params)
        if scanner_params:
            set_scanner_params(scanner_params)

        # Prompt overrides
        if genome.prompt_overrides:
            set_prompt_overrides(genome.prompt_overrides)

        # Code overrides (legacy scoring + strategy hooks)
        if genome.code_overrides and genome.code_overrides.get("candidate_scoring_fn"):
            try:
                fn = load_scoring_function(genome.code_overrides["candidate_scoring_fn"])
                set_custom_scoring_fn(fn)
            except Exception:
                set_custom_scoring_fn(None)
        else:
            set_custom_scoring_fn(None)

        # Strategy hooks
        from invest_system.engine_hooks import get_hook_registry
        from invest_system.evolution.validator import _load_hooks_from_genome
        _load_hooks_from_genome(genome, get_hook_registry())

    @staticmethod
    def rollback(genome_id: str, settings: Settings) -> StrategyGenome | None:
        """Roll back to a previous genome."""
        genome_dir = settings.evolution_genome_dir
        genome = StrategyGenome.load_by_id(genome_id, genome_dir)
        if genome is None:
            raise FileNotFoundError(f"Genome {genome_id} not found")
        applier = GenomeApplier()
        applier.apply(genome, settings)
        genome.save_as_active(genome_dir)
        return genome
