"""Performance analyzer: computes metrics and detects patterns from historical trading data."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import numpy as np


@dataclass
class AnalysisReport:
    report_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    period_start: str = ""
    period_end: str = ""
    trading_days: int = 0

    # core metrics
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    calmar_ratio: float = 0.0
    win_rate_pct: float = 0.0
    avg_trade_return_pct: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    buy_trades: int = 0
    sell_trades: int = 0
    avg_holding_period_days: float = 0.0
    turnover_rate: float = 0.0

    # per-symbol breakdown
    per_symbol_pnl: dict = field(default_factory=dict)
    per_symbol_trades: dict = field(default_factory=dict)

    # pattern analysis
    losing_patterns: list = field(default_factory=list)
    winning_patterns: list = field(default_factory=list)
    phase_performance: dict = field(default_factory=dict)

    # weakness signals
    weakness_signals: list = field(default_factory=list)
    suggested_focus_areas: list = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


class PerformanceAnalyzer:
    def __init__(
        self,
        equity_csv: Path,
        transactions_csv: Path,
        initial_capital: float,
    ):
        self.equity_csv = equity_csv
        self.transactions_csv = transactions_csv
        self.initial_capital = initial_capital

    def analyze(self) -> AnalysisReport:
        report = AnalysisReport()

        eq_df = self._load_equity()
        tx_df = self._load_transactions()

        if eq_df is not None and len(eq_df) > 0:
            self._compute_core_metrics(report, eq_df)
            self._analyze_phases(report, eq_df)

        if tx_df is not None and len(tx_df) > 0:
            self._analyze_trades(report, tx_df)
            self._analyze_per_symbol(report, tx_df)
            self._detect_patterns(report, tx_df)

        self._detect_weakness_signals(report)
        return report

    # --- data loading ---

    def _load_equity(self) -> pd.DataFrame | None:
        if not self.equity_csv.is_file():
            return None
        try:
            return pd.read_csv(self.equity_csv)
        except Exception:
            return None

    def _load_transactions(self) -> pd.DataFrame | None:
        if not self.transactions_csv.is_file():
            return None
        try:
            return pd.read_csv(self.transactions_csv)
        except Exception:
            return None

    # --- core metrics ---

    def _compute_core_metrics(self, report: AnalysisReport, eq_df: pd.DataFrame) -> None:
        equity = eq_df["equity"].values
        dates = eq_df["date"].values if "date" in eq_df.columns else None

        if len(equity) == 0:
            return

        report.period_start = str(dates[0]) if dates is not None else ""
        report.period_end = str(dates[-1]) if dates is not None else ""
        report.trading_days = len(equity)

        # total return
        report.total_return_pct = (equity[-1] / self.initial_capital - 1) * 100

        # annualized return
        if report.trading_days > 1:
            years = report.trading_days / 252
            if years > 0:
                report.annualized_return_pct = (
                    (equity[-1] / equity[0]) ** (1 / years) - 1
                ) * 100

        # daily returns for sharpe
        if len(equity) > 1:
            daily_returns = np.diff(equity) / equity[:-1]
            if np.std(daily_returns) > 0:
                report.sharpe_ratio = (
                    np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252)
                )

        # max drawdown
        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak * 100
        report.max_drawdown_pct = float(np.min(drawdown))

        # calmar ratio
        if report.max_drawdown_pct != 0:
            report.calmar_ratio = abs(
                report.annualized_return_pct / report.max_drawdown_pct
            )

    # --- trade analysis ---

    def _analyze_trades(self, report: AnalysisReport, tx_df: pd.DataFrame) -> None:
        report.total_trades = len(tx_df)
        report.buy_trades = int((tx_df["side"] == "buy").sum())
        report.sell_trades = int((tx_df["side"] == "sell").sum())

        sells = tx_df[tx_df["side"] == "sell"]

        if len(sells) == 0:
            return

        # win rate
        realized = sells["realized_pnl"].dropna()
        if len(realized) > 0:
            report.win_rate_pct = float((realized > 0).sum() / len(realized) * 100)

        # avg trade return
        pnl_pct = sells["realized_pnl_pct"].dropna()
        if len(pnl_pct) > 0:
            report.avg_trade_return_pct = float(pnl_pct.mean())

        # profit factor
        if len(realized) > 0:
            total_wins = float(realized[realized > 0].sum())
            total_losses = abs(float(realized[realized < 0].sum()))
            if total_losses > 0:
                report.profit_factor = total_wins / total_losses

        # holding period (match buy-sell pairs by symbol)
        buys = tx_df[tx_df["side"] == "buy"].copy()
        sells_copy = sells.copy()
        if "date" in buys.columns and "date" in sells_copy.columns:
            holding_periods = []
            for sym in buys["symbol"].unique():
                sym_buys = buys[buys["symbol"] == sym].sort_values("date")
                sym_sells = sells_copy[
                    sells_copy["symbol"] == sym
                ].sort_values("date")
                for _, sell_row in sym_sells.iterrows():
                    sell_date = pd.to_datetime(sell_row["date"])
                    prior_buys = sym_buys[
                        pd.to_datetime(sym_buys["date"]) <= sell_date
                    ]
                    if len(prior_buys) > 0:
                        buy_date = pd.to_datetime(prior_buys.iloc[-1]["date"])
                        holding_periods.append((sell_date - buy_date).days)
            if holding_periods:
                report.avg_holding_period_days = float(np.mean(holding_periods))

        # turnover: sum of abs(trade_value) / mean_equity
        trade_values = (tx_df["shares"] * tx_df["price"]).abs()
        # use initial capital as proxy for mean equity if we don't have equity data
        report.turnover_rate = float(trade_values.sum() / self.initial_capital)

    # --- per-symbol ---

    def _analyze_per_symbol(self, report: AnalysisReport, tx_df: pd.DataFrame) -> None:
        sells = tx_df[tx_df["side"] == "sell"]
        per_sym = sells.groupby("symbol").agg(
            total_pnl=("realized_pnl", "sum"),
            trade_count=("realized_pnl", "count"),
            avg_pnl_pct=("realized_pnl_pct", "mean"),
        )
        report.per_symbol_pnl = {
            sym: round(float(row["total_pnl"]), 2)
            for sym, row in per_sym.iterrows()
        }
        report.per_symbol_trades = {
            sym: int(row["trade_count"])
            for sym, row in per_sym.iterrows()
        }

    # --- phase analysis ---

    def _analyze_phases(self, report: AnalysisReport, eq_df: pd.DataFrame) -> None:
        if "phase" not in eq_df.columns:
            return
        phase_perf = {}
        for phase in eq_df["phase"].unique():
            phase_df = eq_df[eq_df["phase"] == phase]
            if len(phase_df) > 0:
                phase_equity = phase_df["equity"].values
                ret = (phase_equity[-1] / phase_equity[0] - 1) * 100 if len(phase_equity) > 1 else 0
                phase_perf[phase] = {
                    "count": len(phase_df),
                    "return_pct": round(float(ret), 2),
                    "avg_equity": round(float(phase_df["equity"].mean()), 2),
                }
        report.phase_performance = phase_perf

    # --- pattern detection ---

    def _detect_patterns(self, report: AnalysisReport, tx_df: pd.DataFrame) -> None:
        sells = tx_df[tx_df["side"] == "sell"]
        if len(sells) == 0:
            return

        realized = sells["realized_pnl"].dropna()
        if len(realized) == 0:
            return

        # winning patterns
        winners = sells[sells["realized_pnl"] > 0]
        if len(winners) > 0:
            report.winning_patterns = [
                {
                    "top_symbols": winners.groupby("symbol")["realized_pnl"]
                    .sum()
                    .sort_values(ascending=False)
                    .head(5)
                    .to_dict(),
                    "avg_pnl_pct": round(float(winners["realized_pnl_pct"].mean()), 2),
                    "count": len(winners),
                }
            ]

        # losing patterns
        losers = sells[sells["realized_pnl"] < 0]
        if len(losers) > 0:
            report.losing_patterns = [
                {
                    "worst_symbols": losers.groupby("symbol")["realized_pnl"]
                    .sum()
                    .sort_values()
                    .head(5)
                    .to_dict(),
                    "avg_pnl_pct": round(float(losers["realized_pnl_pct"].mean()), 2),
                    "count": len(losers),
                }
            ]

    # --- weakness detection ---

    def _detect_weakness_signals(self, report: AnalysisReport) -> None:
        signals = []

        if report.win_rate_pct > 0 and report.win_rate_pct < 40:
            signals.append(
                f"胜率偏低 ({report.win_rate_pct:.1f}%)，需要改善选股或择时"
            )

        if report.profit_factor > 0 and report.profit_factor < 1.0:
            signals.append(
                f"盈亏比 < 1 ({report.profit_factor:.2f})，亏损总额超过盈利"
            )

        if report.max_drawdown_pct < -15:
            signals.append(
                f"最大回撤较大 ({report.max_drawdown_pct:.1f}%)，考虑加强风控"
            )

        if report.turnover_rate > 10:
            signals.append(
                f"换手率偏高 ({report.turnover_rate:.1f}x)，可能过度交易"
            )

        if report.avg_holding_period_days > 0 and report.avg_holding_period_days < 1:
            signals.append("平均持仓时间极短，日内频繁交易可能侵蚀收益")

        if report.total_trades > 0 and report.sell_trades / max(report.buy_trades, 1) > 2:
            signals.append("卖出次数远超买入，可能过早止盈或频繁止损")

        # per-symbol concentration check
        if report.per_symbol_pnl:
            total_pnl = sum(report.per_symbol_pnl.values())
            if total_pnl != 0:
                worst_sym = min(report.per_symbol_pnl, key=report.per_symbol_pnl.get)
                worst_share = abs(report.per_symbol_pnl[worst_sym] / total_pnl)
                if worst_share > 0.5:
                    signals.append(
                        f"单一标的 '{worst_sym}' 亏损占总亏损 {worst_share:.0%}，集中度风险高"
                    )

        report.weakness_signals = signals

        # suggested focus areas for the evolver
        focus = []
        if report.win_rate_pct < 45:
            focus.append("选股质量：提高买入标的的准确性")
        if report.max_drawdown_pct < -10:
            focus.append("风控：降低最大回撤，可调整仓位限制或止损逻辑")
        if report.turnover_rate > 8:
            focus.append("交易频率：减少低效换手")
        if not focus:
            focus.append("微调参数：在已有框架上做小幅优化")
        report.suggested_focus_areas = focus
