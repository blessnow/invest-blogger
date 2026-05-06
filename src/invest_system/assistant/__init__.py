"""短线看盘助手：盘前 / 开盘5分钟 / 午间 / 收盘 — 搜集资讯、生成短文、注入调仓 LLM。"""

from invest_system.assistant.runner import run_intraday_assistant_for_day

__all__ = ["run_intraday_assistant_for_day"]
