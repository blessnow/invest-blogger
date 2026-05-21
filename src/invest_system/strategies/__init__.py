"""可插拔交易策略实现。

当前提供：
- momentum：双均线 + 截面动量 + 大盘择时（无 LLM，A 股经典轮动思路）

入口契约：每个策略导出 `decide(...)` 返回 list[dict]，结构与 LLM 的 actions 一致：
    {"symbol": str, "side": "buy"|"sell", "shares": int, "reason": str}
引擎可直接复用 _apply_actions 执行。
"""
