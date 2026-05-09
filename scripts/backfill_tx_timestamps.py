"""一次性回填：用 live_intraday_equity.csv 里每个 phase 的真实运行时间，
对齐 live_intraday_transactions 历史交易的 timestamp。幂等。

scheduler 启动 upgrade 阶段也会自动跑一次同样的对齐——本脚本仅给本地手动跑用。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from invest_system.config import load_settings  # noqa: E402
from invest_system.live_phase import load_live_portfolio, save_live_portfolio  # noqa: E402
from invest_system.transactions_io import write_transactions_csv  # noqa: E402
from invest_system.tx_backfill import align_transactions_to_equity  # noqa: E402


def main() -> None:
    settings = load_settings()
    state_path = Path(settings.live_portfolio_state_path)
    if not state_path.is_file():
        local = Path("data/live_portfolio_state.json")
        if local.is_file():
            state_path = local
        else:
            print("找不到 state.json：", state_path, file=sys.stderr)
            sys.exit(2)

    prefix = settings.live_equity_csv_prefix.strip() or "live_intraday"
    eq_path = state_path.parent / f"{prefix}_equity.csv"
    tx_path = state_path.parent / f"{prefix}_transactions.csv"

    p = load_live_portfolio(
        state_path,
        initial_capital=float(settings.initial_capital),
        fee_rate=float(settings.commission_rate),
        t_plus_1_enabled=bool(settings.t_plus_1_enabled),
    )
    rewritten = align_transactions_to_equity(p, eq_path)
    save_live_portfolio(state_path, p)
    write_transactions_csv(p, tx_path)
    print(f"loaded={len(p.transactions)} rewritten={rewritten} → state.json + {tx_path.name} 已更新")


if __name__ == "__main__":
    main()
