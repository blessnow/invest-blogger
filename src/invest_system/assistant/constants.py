from __future__ import annotations

# (phase_key, 中文节点名)
INTRADAY_PHASES: tuple[tuple[str, str], ...] = (
    ("pre_open", "盘前"),
    ("open_5m", "开盘5分钟"),
    ("midday", "午间"),
    ("close", "收盘"),
)
