"""Strategy hook registry: pluggable injection points for evolution-generated strategy code.

Five hooks control the trading engine's behavior. Each has a default implementation
matching the original hardcoded logic. Evolution-generated code can override any hook
via the registry; on failure, the default behavior is restored automatically.
"""

from __future__ import annotations

from typing import Any, Callable


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: dict[str, Callable | None] = {
            "should_rebalance": None,
            "score_candidate": None,
            "size_position": None,
            "check_exit": None,
            "filter_risk": None,
        }

    def set_hook(self, name: str, fn: Callable | None) -> None:
        if name not in self._hooks:
            raise ValueError(f"Unknown hook: {name}")
        self._hooks[name] = fn

    def clear_all(self) -> None:
        for name in self._hooks:
            self._hooks[name] = None

    def snapshot(self) -> dict[str, Callable | None]:
        return dict(self._hooks)

    def restore(self, snap: dict[str, Callable | None]) -> None:
        for name in self._hooks:
            self._hooks[name] = snap.get(name)

    # ---- hook dispatchers (each with default fallback) ----

    def should_rebalance(self, ctx: dict) -> bool:
        fn = self._hooks["should_rebalance"]
        if fn is None:
            return ctx.get("days_since_rebalance", 0) >= ctx.get("default_interval", 1)
        try:
            return bool(fn(ctx))
        except Exception:
            return ctx.get("days_since_rebalance", 0) >= ctx.get("default_interval", 1)

    def score_candidate(self, ctx: dict) -> float:
        fn = self._hooks["score_candidate"]
        if fn is None:
            ret1 = ctx.get("ret1d_pct", 0.0)
            ret5 = ctx.get("ret5d_pct", 0.0)
            return 0.65 * ret1 + 0.35 * ret5
        try:
            return float(fn(ctx))
        except Exception:
            return 0.0

    def size_position(self, ctx: dict) -> int:
        fn = self._hooks["size_position"]
        if fn is None:
            return int(ctx.get("requested_shares", 0))
        try:
            return max(0, int(fn(ctx)))
        except Exception:
            return int(ctx.get("requested_shares", 0))

    def check_exit(self, ctx: dict) -> list[dict]:
        fn = self._hooks["check_exit"]
        if fn is None:
            return []
        try:
            result = fn(ctx)
            return result if isinstance(result, list) else []
        except Exception:
            return []

    def filter_risk(self, ctx: dict) -> list[dict]:
        fn = self._hooks["filter_risk"]
        if fn is None:
            return ctx.get("actions", [])
        try:
            result = fn(ctx)
            return result if isinstance(result, list) else ctx.get("actions", [])
        except Exception:
            return ctx.get("actions", [])


_registry = HookRegistry()


def get_hook_registry() -> HookRegistry:
    return _registry


def set_hook_registry(registry: HookRegistry) -> None:
    global _registry
    _registry = registry
