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

from invest_system.cache_janitor import prune_data_cache
from invest_system.config import Settings, load_settings
from invest_system.live_phase import run_live_intraday_phase

LIVE_PHASES = (
    ("pre_open", 9, 20),
    ("open_5m", 9, 35),
    ("midday", 11, 30),
    ("close", 15, 5),
)

DEFAULT_TZ = "Asia/Shanghai"


def _live_phase_job(settings: Settings, phase_key: str) -> None:
    log = logging.getLogger("scheduler.live")
    log.info("running live phase: %s", phase_key)
    try:
        run_live_intraday_phase(settings, phase_key=phase_key)
    except SystemExit as exc:
        log.warning("live %s exited: %s", phase_key, exc)
    except Exception:
        log.exception("live %s failed", phase_key)


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


def build_scheduler(settings: Settings, *, tz: str = DEFAULT_TZ) -> BlockingScheduler:
    sch = BlockingScheduler(timezone=tz)
    for phase, hour, minute in LIVE_PHASES:
        sch.add_job(
            _live_phase_job,
            CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=tz),
            kwargs={"settings": settings, "phase_key": phase},
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
        choices=[p[0] for p in LIVE_PHASES] + ["cache"],
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

    # 启动时对 state.json + transactions.csv 做一次 idempotent 回填升级
    # （加 timestamp 字段、按时间倒序），让看板立即看到新格式，无需等下一个 phase。
    try:
        from invest_system.live_phase import load_live_portfolio, save_live_portfolio
        from invest_system.transactions_io import write_transactions_csv

        state_path = Path(settings.live_portfolio_state_path)
        if state_path.is_file():
            p = load_live_portfolio(
                state_path,
                initial_capital=float(settings.initial_capital),
                fee_rate=float(settings.commission_rate),
                t_plus_1_enabled=bool(settings.t_plus_1_enabled),
            )
            save_live_portfolio(state_path, p)
            prefix = settings.live_equity_csv_prefix.strip() or "live_intraday"
            write_transactions_csv(p, Path(settings.data_dir) / f"{prefix}_transactions.csv")
            log.info("startup upgrade: state + transactions.csv refreshed (%d txs)", len(p.transactions))
    except Exception:
        log.exception("startup upgrade failed (non-fatal)")

    if args.run_now == "cache":
        _cache_janitor_job(settings)
    elif args.run_now:
        _live_phase_job(settings, args.run_now)

    sch = build_scheduler(settings, tz=args.tz)
    _install_signal_handlers(sch)

    now = datetime.now(ZoneInfo(args.tz)).strftime("%Y-%m-%d %H:%M:%S")
    log.info("scheduler started tz=%s now=%s jobs=%s", args.tz, now, [j.id for j in sch.get_jobs()])
    sch.start()


if __name__ == "__main__":
    main()
