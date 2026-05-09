from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from invest_system.config import load_settings
from invest_system.data_feed import download_prices
from invest_system.engine import run_simulation
from invest_system.reporting import write_equity_report
from invest_system.transactions_io import write_transactions_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="投资模拟：DeepSeek 策略 + 真实行情（Yahoo）")
    parser.add_argument(
        "--prefix",
        default=None,
        help="输出文件前缀（默认带时间戳）",
    )
    args = parser.parse_args()

    settings = load_settings()
    symbols = settings.symbols()
    prefix = args.prefix or datetime.now().strftime("%Y%m%d_%H%M%S")

    smode = settings.strategy_mode.strip().lower()
    if smode == "buy_hold" and settings.is_free_selection() and not symbols:
        print(
            "错误: STRATEGY_MODE=buy_hold 且 SELECTION_MODE=free 时，请在 UNIVERSE 中提供建仓标的。",
            file=sys.stderr,
        )
        sys.exit(2)

    def _dedupe(sym_list: list[str]) -> list[str]:
        return list(dict.fromkeys([x for x in sym_list if x]))

    benches = settings.reference_benchmark_symbols()
    cal = settings.calendar_symbol.strip().upper()

    if settings.is_free_selection():
        boot_symbols = _dedupe([cal, *benches, *symbols])
        df = download_prices(
            boot_symbols,
            settings.start_date,
            settings.end_date,
            cache_dir=settings.data_dir,
        )
    else:
        if not symbols:
            print("错误: fixed 模式需要非空 UNIVERSE。", file=sys.stderr)
            sys.exit(2)
        boot_symbols = _dedupe([*benches, *symbols])
        df = download_prices(
            boot_symbols,
            settings.start_date,
            settings.end_date,
            cache_dir=settings.data_dir,
        )

    portfolio, snapshots, assistant_dirs = run_simulation(settings, df)
    csv_path, png_path = write_equity_report(
        snapshots,
        float(settings.initial_capital),
        settings.data_dir,
        prefix=prefix,
    )
    tx_path = write_transactions_csv(
        portfolio,
        settings.data_dir / f"{prefix}_transactions.csv",
    )

    last = snapshots[-1] if snapshots else None
    sel = "free" if settings.is_free_selection() else "fixed"
    print(f"选股: SELECTION_MODE={sel}  初始下载代码: {boot_symbols}")
    print(f"参考基数: {settings.reference_benchmark_symbols()}")
    print(f"区间: {settings.start_date} ~ {settings.end_date}  STRATEGY_MODE={settings.strategy_mode}")
    print(f"期末权益: {last.equity:.2f}" if last else "无快照")
    print(f"交易笔数: {len(portfolio.transactions)}")
    print(f"权益CSV: {csv_path}")
    print(f"图表: {png_path}")
    print(f"成交CSV: {tx_path}")

    if settings.cache_prune_enabled:
        from invest_system.cache_janitor import prune_data_cache

        try:
            stats = prune_data_cache(
                Path(settings.data_dir),
                max_age_days=int(settings.cache_prune_max_age_days),
            )
            if stats.get("removed"):
                mb = stats["bytes_freed"] / (1024 * 1024)
                print(f"缓存清理：删除 {stats['removed']} 个 pkl，释放 {mb:.2f} MB")
        except Exception as exc:
            print(f"缓存清理异常：{type(exc).__name__}: {exc}", file=sys.stderr)

    root = Path(settings.assistant_artifacts_dir).resolve()
    if assistant_dirs:
        print(f"看盘助手: 已生成 {len(assistant_dirs)} 个交易日，根目录 {root}")
        for p in assistant_dirs[:8]:
            print(f"  · {p.resolve()} → day_bundle.md / *_article.md")
        if len(assistant_dirs) > 8:
            print(f"  · … 另有 {len(assistant_dirs) - 8} 个日期目录")
    elif settings.strategy_mode.strip().lower() == "llm":
        print(
            "看盘助手: 本次未生成博文。需要同时在 .env 中设置 "
            "INTRADAY_ASSISTANT=true（且 STRATEGY_MODE=llm，并在调仓日触发）。"
            f" 启用后文件在 {root}/<交易日>/"
        )


if __name__ == "__main__":
    main()
