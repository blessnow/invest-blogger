"""程序内置常驻调度（APScheduler）：

替代 macOS launchd / Linux cron，可在任何 12-Factor 平台（如 Railway）作为 worker 进程长跑。

定时任务：
  · A 股盘中 4 个节点（pre_open/open_5m/midday/close，周一至周五）
  · 行情缓存清理（每天 16:30）
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from invest_system.broker import Broker, create_broker
from invest_system.cache_janitor import prune_data_cache
from invest_system.config import Settings, load_settings
from invest_system.live_phase import run_live_intraday_phase
from invest_system.live_rotation import run_live_rotation

LIVE_PHASES = (
    ("pre_open", 9, 20),
    ("open_5m", 9, 35),
    ("midday", 11, 30),
    ("close", 15, 5),
)

# rotation 策略：每天 14:45（收盘前 15 分钟）调仓
ROTATION_TIME = (14, 45)

DEFAULT_TZ = "Asia/Shanghai"


def _live_phase_job(settings: Settings, phase_key: str, broker: Broker | None = None) -> None:
    log = logging.getLogger("scheduler.live")
    log.info("running live phase: %s", phase_key)
    try:
        run_live_intraday_phase(settings, phase_key=phase_key)
    except SystemExit as exc:
        log.warning("live %s exited: %s", phase_key, exc)
    except Exception:
        log.exception("live %s failed", phase_key)


def _rotation_job(settings: Settings, broker: Broker | None = None) -> None:
    log = logging.getLogger("scheduler.rotation")
    log.info("running rotation 14:45 调仓")
    try:
        summary = run_live_rotation(settings, broker=broker)
        log.info("rotation done: equity=%s holdings=%s actions=%d",
                 summary.get("equity"), summary.get("num_holdings"),
                 len(summary.get("actions") or []))
    except Exception:
        log.exception("rotation job failed")


def _cache_janitor_job(settings: Settings) -> None:
    log = logging.getLogger("scheduler.cache")
    try:
        stats = prune_data_cache(
            Path(settings.data_dir),
            max_age_days=int(settings.cache_prune_max_age_days),
        )
        mb = stats.get("bytes_freed", 0) / (1024 * 1024)
        log.info(
            "prune scanned=%s removed=%s freed=%.2fMB",
            stats.get("scanned"),
            stats.get("removed"),
            mb,
        )
    except Exception:
        log.exception("cache janitor failed")


def build_scheduler(settings: Settings, *, tz: str = DEFAULT_TZ, broker: Broker | None = None) -> BlockingScheduler:
    sch = BlockingScheduler(timezone=tz)
    for phase, hour, minute in LIVE_PHASES:
        sch.add_job(
            _live_phase_job,
            CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=tz),
            kwargs={"settings": settings, "phase_key": phase, "broker": broker},
            id=f"live_{phase}",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
        )
    sch.add_job(
        _cache_janitor_job,
        CronTrigger(hour=16, minute=30, timezone=tz),
        kwargs={"settings": settings},
        id="cache_janitor",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # rotation 策略：14:45 调仓（仅 STRATEGY_MODE=rotation 或显式开启时）
    rotation_enabled = (
        settings.strategy_mode.strip().lower() == "rotation"
        or str(getattr(settings, "rotation_live_enabled", "")).strip().lower() in ("1","true","yes")
    )
    if rotation_enabled:
        sch.add_job(
            _rotation_job,
            CronTrigger(day_of_week="mon-fri",
                        hour=ROTATION_TIME[0], minute=ROTATION_TIME[1],
                        timezone=tz),
            kwargs={"settings": settings, "broker": broker},
            id="live_rotation",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=1800,
        )

    # Evolution cycle (disabled by default, enable via EVOLUTION_ENABLED=true)
    if getattr(settings, "evolution_enabled", False):
        def _evolution_job(settings: Settings) -> None:
            log = logging.getLogger("scheduler.evolution")
            try:
                from invest_system.evolution.evolver import run_evolution_cycle
                result = run_evolution_cycle(settings)
                if result:
                    log.info("evolution accepted: genome=%s gen=%d", result.genome_id, result.generation)
                else:
                    log.info("evolution cycle complete, no changes accepted")
            except Exception:
                log.exception("evolution cycle failed")

        cron_expr = getattr(settings, "evolution_schedule_cron", "0 18 * * 5").strip()
        parts = cron_expr.split()
        if len(parts) == 5:
            sch.add_job(
                _evolution_job,
                CronTrigger(
                    minute=parts[0], hour=parts[1],
                    day=parts[2], month=parts[3],
                    day_of_week=parts[4], timezone=tz,
                ),
                kwargs={"settings": settings},
                id="evolution_cycle",
                replace_existing=True,
                coalesce=True,
                misfire_grace_time=7200,
            )

    return sch


def _install_signal_handlers(sch: BlockingScheduler) -> None:
    def _stop(_signum, _frame) -> None:
        logging.getLogger("scheduler").info("shutdown signal received")
        try:
            sch.shutdown(wait=False)
        except Exception:
            logging.getLogger("scheduler").exception("shutdown error")
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="invest-system 内置定时服务（APScheduler）")
    parser.add_argument("--tz", default=DEFAULT_TZ, help="时区，默认 Asia/Shanghai")
    parser.add_argument(
        "--run-now",
        choices=[p[0] for p in LIVE_PHASES] + ["cache", "rotation"],
        help="立即执行一次指定任务后再常驻（用于排错）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    settings = load_settings()
    log = logging.getLogger("scheduler")

    if settings.seed_data_enabled:
        from invest_system.bootstrap import ensure_data_seeded

        ensure_data_seeded(Path(settings.data_dir), Path(settings.seed_data_dir), log=log)
        ensure_data_seeded(
            Path(settings.assistant_artifacts_dir),
            Path(settings.seed_data_dir) / "articles",
            log=log,
        )

    # 启动时对 state.json + transactions.csv 做一次 idempotent 回填升级：
    #  1) 旧记录补上 timestamp 字段（缺则 day+15:00 兜底）
    #  2) 用 equity.csv 的真实 phase datetime 对齐 timestamp（覆盖兜底值，逐 day 幂等）
    # 这样 Railway/线上 volume 的旧数据立即看到精准时间，无需等下一个 phase。
    try:
        from invest_system.live_phase import load_live_portfolio, save_live_portfolio
        from invest_system.transactions_io import write_transactions_csv
        from invest_system.tx_backfill import align_transactions_to_equity

        state_path = Path(settings.live_portfolio_state_path)
        if state_path.is_file():
            p = load_live_portfolio(
                state_path,
                initial_capital=float(settings.initial_capital),
                fee_rate=float(settings.commission_rate),
                t_plus_1_enabled=bool(settings.t_plus_1_enabled),
            )
            prefix = settings.live_equity_csv_prefix.strip() or "live_intraday"
            eq_path = Path(settings.data_dir) / f"{prefix}_equity.csv"
            rewritten = align_transactions_to_equity(p, eq_path)
            save_live_portfolio(state_path, p)
            write_transactions_csv(p, Path(settings.data_dir) / f"{prefix}_transactions.csv")
            log.info(
                "startup upgrade: state + transactions.csv refreshed (txs=%d, ts_aligned=%d)",
                len(p.transactions),
                rewritten,
            )
    except Exception:
        log.exception("startup upgrade failed (non-fatal)")

    # 启动时加载进化系统的 active genome（如有），使进化结果在重启后仍然生效
    try:
        from invest_system.evolution.genome import StrategyGenome
        from invest_system.evolution.applier import GenomeApplier

        genome_dir = getattr(settings, "evolution_genome_dir", Path("./data/evolution"))
        if isinstance(genome_dir, str):
            genome_dir = Path(genome_dir)
        active = StrategyGenome.load_active(genome_dir)
        if active and active.validation.accepted:
            applier = GenomeApplier()
            applier.apply(active, settings)
            log.info(
                "startup: loaded active genome %s (gen %d)",
                active.genome_id,
                active.generation,
            )
    except Exception:
        log.exception("startup genome load failed (non-fatal)")

    if args.run_now == "cache":
        _cache_janitor_job(settings)
    elif args.run_now == "rotation":
        _rotation_job(settings, broker=None)
    elif args.run_now:
        broker_mode = getattr(settings, "broker_mode", "paper").strip().lower()
        broker = None
        if broker_mode != "paper":
            from invest_system.live_phase import load_live_portfolio
            portfolio = load_live_portfolio(
                state_path,
                initial_capital=float(settings.initial_capital),
                fee_rate=float(settings.commission_rate),
                t_plus_1_enabled=bool(settings.t_plus_1_enabled),
            )
            broker = create_broker(broker_mode, portfolio, settings)
        _live_phase_job(settings, args.run_now, broker)

    broker_mode = getattr(settings, "broker_mode", "paper").strip().lower()
    broker = None
    if broker_mode != "paper":
        from invest_system.live_phase import load_live_portfolio
        portfolio = load_live_portfolio(
            state_path,
            initial_capital=float(settings.initial_capital),
            fee_rate=float(settings.commission_rate),
            t_plus_1_enabled=bool(settings.t_plus_1_enabled),
        )
        broker = create_broker(broker_mode, portfolio, settings)
        log.info("broker mode: %s", broker_mode)

    sch = build_scheduler(settings, tz=args.tz, broker=broker)
    _install_signal_handlers(sch)

    now = datetime.now(ZoneInfo(args.tz)).strftime("%Y-%m-%d %H:%M:%S")
    log.info("scheduler started tz=%s now=%s jobs=%s broker=%s", args.tz, now, [j.id for j in sch.get_jobs()], broker_mode)
    sch.start()


if __name__ == "__main__":
    main()
