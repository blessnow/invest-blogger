"""结构化错误落盘 — 取代静默 except: pass。

事件以 JSON Lines 追加写入 data/phase_errors.jsonl，Dashboard 可读取展示。
原始 LLM 响应（含解析失败的）落盘到 data/llm_raw/ 便于事后回查。
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


def _data_dir() -> Path:
    from invest_system.config import load_settings

    return Path(load_settings().data_dir)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def log_error(
    *,
    component: str,
    phase: str = "",
    symbol: str = "",
    error: BaseException | None = None,
    message: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """追加一条结构化错误到 data/phase_errors.jsonl。

    Args:
        component: 模块名 (engine / live_phase / llm_strategy / market_scanner ...)
        phase: 阶段标识 (pre_open / open_5m / midday / close / backtest)
        symbol: 相关股票代码 (可选)
        error: 异常对象 (可选)
        message: 自定义说明
        extra: 任意额外字段
    """
    record: dict[str, Any] = {
        "ts": _now_iso(),
        "component": component,
        "phase": phase,
        "symbol": symbol,
        "message": message,
    }
    if error is not None:
        record["error_type"] = type(error).__name__
        record["error_msg"] = str(error)
        record["traceback"] = traceback.format_exc(limit=4)
    if extra:
        record["extra"] = extra

    try:
        d = _data_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / "phase_errors.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # 落盘失败也不能炸主流程
        print(
            f"[errors.py] failed to write error log: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

    # 同时打到 stderr 便于即时可见
    summary = f"[{component}/{phase}] {message}"
    if error is not None:
        summary += f" :: {type(error).__name__}: {error}"
    if symbol:
        summary += f" (symbol={symbol})"
    print(summary, file=sys.stderr)


def dump_llm_raw(
    *,
    phase: str,
    day: str,
    system_prompt: str,
    user_prompt: str,
    raw_response: str,
    error: BaseException | None = None,
) -> Path | None:
    """落盘一次 LLM 调用的原始上下文，便于 JSON 解析失败时回查。

    文件路径：data/llm_raw/<day>/<phase>_<HHMMSS>.json
    """
    try:
        d = _data_dir() / "llm_raw" / day
        d.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%H%M%S")
        path = d / f"{phase or 'unknown'}_{ts}.json"
        payload = {
            "ts": _now_iso(),
            "phase": phase,
            "day": day,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "raw_response": raw_response,
            "error": (
                {"type": type(error).__name__, "msg": str(error)} if error else None
            ),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except Exception as exc:
        print(
            f"[errors.py] failed to dump llm_raw: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None


def read_recent_errors(limit: int = 100) -> list[dict[str, Any]]:
    """读取最近 N 条错误（Dashboard 用）。返回按时间倒序。"""
    path = _data_dir() / "phase_errors.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.reverse()
    return out
