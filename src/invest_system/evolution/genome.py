"""Strategy genome: versioned snapshot of all evolvable strategy parameters."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class GenomeValidation:
    total_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    total_trades: int = 0
    backtest_start: str = ""
    backtest_end: str = ""
    accepted: bool = False
    accepted_at: str | None = None


@dataclass
class GenomeMetadata:
    evolution_reason: str = ""
    mutation_summary: str = ""
    analysis_report_id: str | None = None


@dataclass
class StrategyGenome:
    genome_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    parent_id: str | None = None
    generation: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    selection_mode: str = "fixed"

    config_overrides: dict = field(default_factory=lambda: {
        "max_position_fraction": 0.30,
        "rebalance_every_days": 1,
        "market_candidates_top_n": 20,
        "llm_temperature": 0.2,
    })

    hardcoded_param_overrides: dict = field(default_factory=lambda: {
        "candidate_score_weight_ret1d": 0.65,
        "candidate_score_weight_ret5d": 0.35,
        "recent_bars_lookback": 15,
        "watchlist_cap": 27,
        "deploy_fraction": 0.95,
        "scanner_score_weight_chg": 0.65,
        "scanner_score_weight_amt": 0.35,
    })

    prompt_overrides: dict = field(default_factory=dict)
    code_overrides: dict = field(default_factory=dict)  # populated by evolver with hook keys

    validation: GenomeValidation = field(default_factory=GenomeValidation)
    metadata: GenomeMetadata = field(default_factory=GenomeMetadata)

    # --- serialization ---

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> StrategyGenome:
        d = json.loads(text)
        d["validation"] = GenomeValidation(**d.pop("validation", {}))
        d["metadata"] = GenomeMetadata(**d.pop("metadata", {}))
        return cls(**d)

    # --- persistence ---

    def save(self, genome_dir: Path) -> Path:
        genome_dir.mkdir(parents=True, exist_ok=True)
        path = genome_dir / "genomes" / f"{self.genome_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    def save_as_active(self, genome_dir: Path) -> Path:
        genome_dir.mkdir(parents=True, exist_ok=True)
        path = genome_dir / "active_genome.json"
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    def append_history(self, genome_dir: Path) -> Path:
        genome_dir.mkdir(parents=True, exist_ok=True)
        path = genome_dir / "genome_history.jsonl"
        entry = {
            "genome_id": self.genome_id,
            "parent_id": self.parent_id,
            "generation": self.generation,
            "accepted": self.validation.accepted,
            "timestamp": self.created_at,
            "mutation_summary": self.metadata.mutation_summary,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return path

    @classmethod
    def load_active(cls, genome_dir: Path) -> StrategyGenome | None:
        path = genome_dir / "active_genome.json"
        if not path.is_file():
            return None
        return cls.from_json(path.read_text(encoding="utf-8"))

    @classmethod
    def load_by_id(cls, genome_id: str, genome_dir: Path) -> StrategyGenome | None:
        path = genome_dir / "genomes" / f"{genome_id}.json"
        if not path.is_file():
            return None
        return cls.from_json(path.read_text(encoding="utf-8"))

    @classmethod
    def load_history(cls, genome_dir: Path, limit: int = 20) -> list[dict]:
        path = genome_dir / "genome_history.jsonl"
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        entries = [json.loads(line) for line in lines if line.strip()]
        return entries[-limit:]

    # --- mutation ---

    def child(self, mutations: dict | None = None) -> StrategyGenome:
        """Create a mutated child genome from this one."""
        child = StrategyGenome(
            parent_id=self.genome_id,
            generation=self.generation + 1,
            selection_mode=self.selection_mode,
            config_overrides=dict(self.config_overrides),
            hardcoded_param_overrides=dict(self.hardcoded_param_overrides),
            prompt_overrides=dict(self.prompt_overrides),
            code_overrides=dict(self.code_overrides),
            validation=GenomeValidation(),
            metadata=GenomeMetadata(),
        )
        if mutations:
            for section, values in mutations.items():
                if section == "config_overrides" and isinstance(values, dict):
                    child.config_overrides.update(values)
                elif section == "hardcoded_param_overrides" and isinstance(values, dict):
                    child.hardcoded_param_overrides.update(values)
                elif section == "prompt_overrides" and isinstance(values, dict):
                    child.prompt_overrides.update(values)
                elif section == "code_overrides" and isinstance(values, dict):
                    child.code_overrides.update(values)
        return child
