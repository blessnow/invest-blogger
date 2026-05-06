from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str = Field(default="", validation_alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias="DEEPSEEK_BASE_URL",
    )
    deepseek_model: str = Field(
        default="deepseek-chat",
        validation_alias="DEEPSEEK_MODEL",
    )
    #: 相对 BASE；官方控制台 curl 多为 chat/completions（无 v1）。若你使用 OpenAI 兼容客户端则常是 v1/chat/completions
    deepseek_chat_path: str = Field(
        default="chat/completions",
        validation_alias="DEEPSEEK_CHAT_PATH",
    )
    deepseek_stream: bool = Field(default=False, validation_alias="DEEPSEEK_STREAM")
    #: 非空时写入 JSON：thinking = {"type": "<值>"}，如 enabled（deepseek-v4-pro 等）
    deepseek_thinking_type: str = Field(default="", validation_alias="DEEPSEEK_THINKING_TYPE")
    #: 如 high / medium / low
    deepseek_reasoning_effort: str = Field(default="", validation_alias="DEEPSEEK_REASONING_EFFORT")
    #: 合并进请求体的合法 JSON 对象（优先级低于同名键时请避免冲突）
    deepseek_extra_json: str = Field(default="", validation_alias="DEEPSEEK_EXTRA_JSON")

    initial_capital: float = Field(default=100_000.0, validation_alias="INITIAL_CAPITAL")
    universe: str = Field(
        default="600519.SS,601318.SS,300750.SZ",
        validation_alias="UNIVERSE",
    )
    start_date: str = Field(default="2024-01-01", validation_alias="START_DATE")
    end_date: str = Field(default="2024-12-31", validation_alias="END_DATE")

    data_dir: Path = Field(default=Path("./data"), validation_alias="DATA_DIR")

    rebalance_every_days: int = Field(default=1, validation_alias="REBALANCE_EVERY_DAYS")
    strategy_mode: str = Field(default="llm", validation_alias="STRATEGY_MODE")
    max_position_fraction: float = Field(
        default=0.30,
        validation_alias="MAX_POSITION_FRACTION",
    )
    lot_size: int = Field(
        default=100,
        validation_alias="LOT_SIZE",
    )
    commission_rate: float = Field(
        default=0.00025,
        validation_alias="COMMISSION_RATE",
    )
    selection_mode: str = Field(
        default="fixed",
        validation_alias="SELECTION_MODE",
    )
    calendar_symbol: str = Field(
        default="000001.SS",
        validation_alias="CALENDAR_SYMBOL",
    )
    reference_benchmarks: str = Field(
        default="000300.SS,510500.SS,588000.SS",
        validation_alias="REFERENCE_BENCHMARKS",
    )
    market_context_url: str = Field(
        default="",
        validation_alias="MARKET_CONTEXT_URL",
    )
    market_context_timeout_sec: float = Field(
        default=8.0,
        validation_alias="MARKET_CONTEXT_TIMEOUT_SEC",
    )
    market_context_max_chars: int = Field(
        default=8000,
        validation_alias="MARKET_CONTEXT_MAX_CHARS",
    )
    market_scan_universe: str = Field(default="", validation_alias="MARKET_SCAN_UNIVERSE")
    market_candidates_top_n: int = Field(default=20, validation_alias="MARKET_CANDIDATES_TOP_N")
    free_selection_enforce_candidates: bool = Field(
        default=True,
        validation_alias="FREE_SELECTION_ENFORCE_CANDIDATES",
    )
    intraday_quote_enabled: bool = Field(default=True, validation_alias="INTRADAY_QUOTE_ENABLED")
    intraday_quote_period: str = Field(default="1d", validation_alias="INTRADAY_QUOTE_PERIOD")
    intraday_quote_interval: str = Field(default="1m", validation_alias="INTRADAY_QUOTE_INTERVAL")
    market_scan_retries: int = Field(default=3, validation_alias="MARKET_SCAN_RETRIES")
    market_scan_cache_max_age_min: int = Field(
        default=240,
        validation_alias="MARKET_SCAN_CACHE_MAX_AGE_MIN",
    )
    market_scan_http_proxy: str = Field(default="", validation_alias="MARKET_SCAN_HTTP_PROXY")
    market_scan_https_proxy: str = Field(default="", validation_alias="MARKET_SCAN_HTTPS_PROXY")
    market_scan_no_proxy: str = Field(default="", validation_alias="MARKET_SCAN_NO_PROXY")
    market_scan_probe_url: str = Field(default="", validation_alias="MARKET_SCAN_PROBE_URL")
    market_scan_probe_timeout_sec: float = Field(
        default=3.0,
        validation_alias="MARKET_SCAN_PROBE_TIMEOUT_SEC",
    )

    intraday_assistant: str = Field(default="false", validation_alias="INTRADAY_ASSISTANT")
    assistant_artifacts_dir: Path = Field(
        default=Path("./artifacts/assistant"),
        validation_alias="ASSISTANT_ARTIFACTS_DIR",
    )
    assistant_rss_urls: str = Field(default="", validation_alias="ASSISTANT_RSS_URLS")
    assistant_gather_url: str = Field(default="", validation_alias="ASSISTANT_GATHER_URL")
    assistant_model: str = Field(default="", validation_alias="ASSISTANT_MODEL")
    assistant_temperature: float = Field(default=0.55, validation_alias="ASSISTANT_TEMPERATURE")
    assistant_http_timeout_sec: float = Field(
        default=15.0,
        validation_alias="ASSISTANT_HTTP_TIMEOUT_SEC",
    )
    assistant_max_rss_items_total: int = Field(
        default=24,
        validation_alias="ASSISTANT_MAX_RSS_ITEMS_TOTAL",
    )
    assistant_max_rss_feeds: int = Field(
        default=12,
        validation_alias="ASSISTANT_MAX_RSS_FEEDS",
    )
    assistant_max_bundle_chars: int = Field(
        default=12000,
        validation_alias="ASSISTANT_MAX_BUNDLE_CHARS",
    )
    assistant_llm_timeout_sec: float = Field(
        default=120.0,
        validation_alias="ASSISTANT_LLM_TIMEOUT_SEC",
    )
    assistant_news_search_enabled: bool = Field(
        default=True,
        validation_alias="ASSISTANT_NEWS_SEARCH_ENABLED",
    )
    assistant_news_search_queries: str = Field(
        default="A股,沪深,算力,半导体,存储芯片,北向资金,政策",
        validation_alias="ASSISTANT_NEWS_SEARCH_QUERIES",
    )
    assistant_max_search_queries: int = Field(
        default=8,
        validation_alias="ASSISTANT_MAX_SEARCH_QUERIES",
    )
    assistant_max_items_per_topic: int = Field(
        default=4,
        validation_alias="ASSISTANT_MAX_ITEMS_PER_TOPIC",
    )
    assistant_fetch_article_body: bool = Field(
        default=True,
        validation_alias="ASSISTANT_FETCH_ARTICLE_BODY",
    )
    assistant_max_articles_body_fetch: int = Field(
        default=5,
        validation_alias="ASSISTANT_MAX_ARTICLES_BODY_FETCH",
    )
    assistant_article_body_max_chars: int = Field(
        default=1500,
        validation_alias="ASSISTANT_ARTICLE_BODY_MAX_CHARS",
    )
    assistant_article_body_timeout_sec: float = Field(
        default=15.0,
        validation_alias="ASSISTANT_ARTICLE_BODY_TIMEOUT_SEC",
    )

    #: 盘中定时任务持久化模拟持仓（invest_system.live_phase）
    live_portfolio_state_path: Path = Field(
        default=Path("./data/live_portfolio_state.json"),
        validation_alias="LIVE_PORTFOLIO_STATE_PATH",
    )
    live_equity_csv_prefix: str = Field(
        default="live_intraday",
        validation_alias="LIVE_EQUITY_CSV_PREFIX",
    )

    def symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.universe.split(",") if s.strip()]

    def is_free_selection(self) -> bool:
        return self.selection_mode.strip().lower() == "free"

    def reference_benchmark_symbols(self) -> list[str]:
        return [
            s.strip().upper()
            for s in self.reference_benchmarks.split(",")
            if s.strip()
        ]

    def market_scan_symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.market_scan_universe.split(",") if s.strip()]

    def intraday_assistant_enabled(self) -> bool:
        return self.intraday_assistant.strip().lower() in ("1", "true", "yes", "on")

    def assistant_llm_model(self) -> str:
        m = self.assistant_model.strip()
        return m if m else self.deepseek_model

    def deepseek_completions_url(self) -> str:
        base = self.deepseek_base_url.strip().rstrip("/")
        path = self.deepseek_chat_path.strip().lstrip("/")
        return f"{base}/{path}"


def load_settings() -> Settings:
    return Settings()
