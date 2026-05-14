"""CLI entry point for invest-evolve command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_settings() -> "Settings":
    from invest_system.config import Settings
    return Settings()


def cmd_analyze(settings: "Settings", args: argparse.Namespace) -> None:
    from invest_system.evolution.analyzer import PerformanceAnalyzer

    genome_dir = settings.evolution_genome_dir
    equity_csv = settings.data_dir / f"{settings.live_equity_csv_prefix}_equity.csv"
    tx_csv = settings.data_dir / f"{settings.live_equity_csv_prefix}_transactions.csv"

    analyzer = PerformanceAnalyzer(
        equity_csv=equity_csv,
        transactions_csv=tx_csv,
        initial_capital=float(settings.initial_capital),
    )
    report = analyzer.analyze()

    reports_dir = genome_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{report.report_id}.json"
    report_path.write_text(report.to_json(), encoding="utf-8")

    print(f"Report saved: {report_path}")
    print(f"  Total Return: {report.total_return_pct:.2f}%")
    print(f"  Sharpe Ratio: {report.sharpe_ratio:.3f}")
    print(f"  Max Drawdown: {report.max_drawdown_pct:.2f}%")
    print(f"  Win Rate: {report.win_rate_pct:.1f}%")
    print(f"  Total Trades: {report.total_trades}")
    if report.weakness_signals:
        print("  Weakness Signals:")
        for s in report.weakness_signals:
            print(f"    - {s}")
    if report.suggested_focus_areas:
        print("  Suggested Focus Areas:")
        for a in report.suggested_focus_areas:
            print(f"    - {a}")


def cmd_evolve(settings: "Settings", args: argparse.Namespace) -> None:
    from invest_system.evolution.evolver import run_evolution_cycle

    result = run_evolution_cycle(settings)
    if result:
        print(f"\nEvolution complete. New genome: {result.genome_id} (gen {result.generation})")
    else:
        print("\nEvolution complete. No changes accepted.")


def cmd_status(settings: "Settings", args: argparse.Namespace) -> None:
    from invest_system.evolution.genome import StrategyGenome

    genome_dir = settings.evolution_genome_dir
    active = StrategyGenome.load_active(genome_dir)
    if active:
        print(f"Active Genome: {active.genome_id}")
        print(f"  Generation: {active.generation}")
        print(f"  Parent: {active.parent_id}")
        print(f"  Created: {active.created_at}")
        print(f"  Config: {json.dumps(active.config_overrides, indent=2)}")
        print(f"  Hardcoded: {json.dumps(active.hardcoded_param_overrides, indent=2)}")
        print(f"  Validation: accepted={active.validation.accepted}, "
              f"return={active.validation.total_return_pct:.2f}%, "
              f"sharpe={active.validation.sharpe_ratio:.3f}")
    else:
        print("No active genome. Using defaults.")


def cmd_history(settings: "Settings", args: argparse.Namespace) -> None:
    from invest_system.evolution.genome import StrategyGenome

    limit = args.limit or 20
    history = StrategyGenome.load_history(settings.evolution_genome_dir, limit=limit)
    if not history:
        print("No evolution history.")
        return
    print(f"Evolution History (last {len(history)}):")
    for h in reversed(history):
        status = "ACCEPTED" if h.get("accepted") else "REJECTED"
        print(f"  [{h.get('timestamp', '?')[:19]}] {h.get('genome_id', '?')} "
              f"gen{h.get('generation', '?')} {status} — {h.get('mutation_summary', 'N/A')}")


def cmd_rollback(settings: "Settings", args: argparse.Namespace) -> None:
    from invest_system.evolution.applier import GenomeApplier

    genome_id = args.genome_id
    result = GenomeApplier.rollback(genome_id, settings)
    print(f"Rolled back to genome {result.genome_id} (gen {result.generation})")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="invest-evolve",
        description="Strategy self-evolution engine",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("analyze", help="Run performance analysis only")
    sub.add_parser("evolve", help="Run full evolution cycle (default)")
    sub.add_parser("status", help="Show active genome")

    hist = sub.add_parser("history", help="Show evolution history")
    hist.add_argument("--limit", type=int, default=20)

    rb = sub.add_parser("rollback", help="Roll back to a previous genome")
    rb.add_argument("genome_id", help="Genome ID to roll back to")

    args = parser.parse_args()
    settings = _load_settings()

    # Default to full evolve if no subcommand
    cmd = args.command or "evolve"

    dispatch = {
        "analyze": cmd_analyze,
        "evolve": cmd_evolve,
        "status": cmd_status,
        "history": cmd_history,
        "rollback": cmd_rollback,
    }
    handler = dispatch.get(cmd)
    if handler:
        handler(settings, args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
