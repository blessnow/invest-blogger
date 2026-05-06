"""参考基准：沪深300 / 中证500 / 科创50（Yahoo 代码）。

说明：yfinance 对部分上证指数代码不稳定；中证500、科创50默认用流动性好的 ETF 作行情代理，
跟踪标的与官方指数一致，便于模型看相对强弱。可通过 REFERENCE_BENCHMARKS 覆盖。
"""

from __future__ import annotations

# 默认：300 用指数；500 / 科创50 用主流 ETF（与指数走势高度一致）
DEFAULT_REFERENCE_BENCHMARKS = ("000300.SS", "510500.SS", "588000.SS")

REF_BENCHMARK_LABELS: dict[str, str] = {
    "000300.SS": "沪深300指数",
    "510500.SS": "中证500（ETF，跟踪中证500）",
    "588000.SS": "科创50（ETF，跟踪科创50）",
    # 常见替代
    "510300.SS": "沪深300ETF",
    "159919.SZ": "沪深300ETF",
    "512500.SS": "中证500ETF",
    "159922.SZ": "中证500ETF",
    "588080.SS": "科创50ETF",
}


def benchmark_label(symbol: str) -> str:
    s = symbol.strip().upper()
    return REF_BENCHMARK_LABELS.get(s, s)


def format_benchmark_prices(prices: dict[str, float], symbols: list[str]) -> str:
    lines: list[str] = []
    for sym in symbols:
        sym_u = sym.strip().upper()
        if not sym_u:
            continue
        label = benchmark_label(sym_u)
        px = prices.get(sym_u)
        if px is not None:
            lines.append(f"  {label} [{sym_u}] = {px:.4f}")
        else:
            lines.append(f"  {label} [{sym_u}] = (当日无收盘/未拉到数据)")
    return "\n".join(lines) if lines else "  (未配置基准)"
