from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _pos_on_or_before(panel: pd.DataFrame, ts: pd.Timestamp) -> int:
    idx = panel.index
    p = idx.searchsorted(ts, side="right") - 1
    return int(p)


def _get_cell(row: Any, sym: str, field: str) -> float | None:
    try:
        v = float(row[(sym, field)])
        if math.isfinite(v):
            return v
    except (KeyError, TypeError, ValueError):
        pass
    return None


def build_phase_market_notes(
    phase: str,
    panel: pd.DataFrame,
    ts: pd.Timestamp,
    symbols: list[str],
    *,
    live_execution_prices: dict[str, float] | None = None,
) -> str:
    """
    仅有日线 OHLCV 时的节点近似说明（非真实分钟线）。
    """
    if panel.empty or not symbols:
        return "（无行情面板）"

    pos = _pos_on_or_before(panel, ts)
    if pos < 0:
        return "（无当日行情）"

    row = panel.iloc[pos]
    prev_row = panel.iloc[pos - 1] if pos > 0 else None

    live = live_execution_prices or {}
    mode_note = (
        "实盘节点：以下 OHLC 仅供趋势参考；模拟撮合价以「实盘执行价快照」为准（Yahoo 分钟线最近价 + 东财快照兜底）。"
        if live
        else "日线回测近似：用 OHLC 代替分时，非真实盘口。"
    )
    lines: list[str] = [
        f"节点={phase}；决策日={ts.date()}；{mode_note}",
        "",
    ]
    if live:
        snap_syms = [s for s in symbols if str(s).strip().upper() in live]
        if not snap_syms:
            snap_syms = list(live.keys())[: min(20, len(live))]
        snap_lines = [
            f"{s.strip().upper()}: {float(live[s.strip().upper()]):.4f}"
            for s in snap_syms
            if live.get(s.strip().upper(), 0) > 0
        ]
        if snap_lines:
            lines.append("【实盘执行价快照】")
            lines.extend(snap_lines[:25])
            lines.append("")

    for sym in symbols:
        sym = sym.strip().upper()
        if not sym:
            continue
        o = _get_cell(row, sym, "Open")
        h = _get_cell(row, sym, "High")
        l = _get_cell(row, sym, "Low")
        c = _get_cell(row, sym, "Close")
        pc = _get_cell(prev_row, sym, "Close") if prev_row is not None else None

        if c is None and o is None:
            lines.append(f"{sym}: (当日无数据)")
            continue

        parts = [f"{sym}:"]
        if pc is not None:
            parts.append(f"昨收={pc:.4f}")
        if phase == "pre_open":
            parts.append("盘前锚点：以上一交易日收盘为主；若有隔夜外围可写在扩展上下文。")
        elif phase == "open_5m":
            if o is not None and pc is not None:
                parts.append(f"今开={o:.4f}；相较昨收 {(o/pc-1)*100:.2f}%")
            elif o is not None:
                parts.append(f"今开={o:.4f}")
            parts.append("近似：用日线开盘价代替开盘后5分钟均价。")
        elif phase == "midday":
            if o is not None and h is not None and l is not None:
                mid = (o + h + l) / 3.0
                parts.append(f"午间近似中枢={(o+h+l)/3:.4f}（(开+高+低)/3）")
                if c is not None:
                    parts.append(f"当日收盘已知={c:.4f}（回测泄漏提示：实盘午间不应看到确切收盘价）")
            parts.append("实盘应换分钟线或实时盘口；此处仅演示管线。")
        else:  # close
            if c is not None:
                parts.append(f"收盘={c:.4f}")
            if l is not None and h is not None:
                parts.append(f"日内高低 [{l:.4f}, {h:.4f}]")
        lines.append(" ".join(p for p in parts if p))

    return "\n".join(lines)
