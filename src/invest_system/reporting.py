from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

plt.rcParams["font.sans-serif"] = [
    "PingFang SC",
    "Arial Unicode MS",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "SimHei",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False

from invest_system.engine import EquitySnapshot


def snapshots_to_frame(snapshots: list[EquitySnapshot], initial_capital: float) -> pd.DataFrame:
    rows = []
    for s in snapshots:
        ret = (s.equity / initial_capital - 1.0) if initial_capital else 0.0
        rows.append(
            {
                "date": s.day,
                "cash": s.cash,
                "equity": s.equity,
                "return_pct": ret * 100.0,
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
    return df


def write_equity_report(
    snapshots: list[EquitySnapshot],
    initial_capital: float,
    out_dir: Path,
    prefix: str = "run",
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    df = snapshots_to_frame(snapshots, initial_capital)
    csv_path = out_dir / f"{prefix}_equity.csv"
    df.to_csv(csv_path)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(df.index, df["equity"], label="组合净值", color="#1f77b4")
    ax.axhline(initial_capital, color="#888", linestyle="--", linewidth=1, label="初始资金")
    ax.set_title("累计权益曲线（模拟盘，真实历史价格成交）")
    ax.set_xlabel("日期")
    ax.set_ylabel("净值")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    png_path = out_dir / f"{prefix}_equity_curve.png"
    fig.savefig(png_path, dpi=150)
    plt.close(fig)
    return csv_path, png_path
