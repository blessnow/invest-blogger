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

    glm_api_key: str = Field(default="", validation_alias="ANTHROPIC_AUTH_TOKEN")
    glm_base_url: str = Field(
        default="https://open.bigmodel.cn/api/paas/v4",
        validation_alias="ANTHROPIC_BASE_URL",
    )
    glm_model: str = Field(
        default="glm-5",
        validation_alias="ANTHROPIC_MODEL",
    )
    glm_chat_path: str = Field(
        default="chat/completions",
        validation_alias="ANTHROPIC_CHAT_PATH",
    )
    glm_stream: bool = Field(default=False, validation_alias="ANTHROPIC_STREAM")
    glm_temperature: float = Field(default=0.2, validation_alias="ANTHROPIC_TEMPERATURE")
    glm_max_tokens: int = Field(default=4096, validation_alias="ANTHROPIC_MAX_TOKENS")

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

    intraday_assistant: str = Field(default="true", validation_alias="INTRADAY_ASSISTANT")
    assistant_artifacts_dir: Path = Field(
        default=Path("./data/articles"),
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

    #: 行情缓存清理（防止 ohlcv_*.pkl / prices_*.pkl 越积越多）
    cache_prune_enabled: bool = Field(default=True, validation_alias="CACHE_PRUNE_ENABLED")
    cache_prune_max_age_days: int = Field(
        default=14,
        validation_alias="CACHE_PRUNE_MAX_AGE_DAYS",
    )

    #: A 股 T+1：当日买入当日不可卖。回测/纸面策略想放开就置 false。
    t_plus_1_enabled: bool = Field(default=True, validation_alias="T_PLUS_1_ENABLED")

    #: 看板登录（部署到 Railway 等公网时启用）
    dashboard_auth_enabled: bool = Field(
        default=False,
        validation_alias="DASHBOARD_AUTH_ENABLED",
    )
    dashboard_users: str = Field(default="", validation_alias="DASHBOARD_USERS")

    #: 首次启动若 DATA_DIR 为空，从此目录播种
    seed_data_dir: Path = Field(
        default=Path("./seed_data"),
        validation_alias="SEED_DATA_DIR",
    )
    seed_data_enabled: bool = Field(default=True, validation_alias="SEED_DATA_ENABLED")

    # ---- Momentum strategy params (STRATEGY_MODE=momentum) ----
    momentum_top_k: int = Field(default=5, validation_alias="MOMENTUM_TOP_K")
    momentum_ret20_min: float = Field(default=5.0, validation_alias="MOMENTUM_RET20_MIN")
    momentum_stop_loss_pct: float = Field(default=8.0, validation_alias="MOMENTUM_STOP_LOSS_PCT")
    momentum_rebalance_every_days: int = Field(
        default=5, validation_alias="MOMENTUM_REBALANCE_EVERY_DAYS"
    )
    momentum_regime_symbol: str = Field(
        default="000300.SS", validation_alias="MOMENTUM_REGIME_SYMBOL"
    )
    momentum_regime_ma_window: int = Field(
        default=200, validation_alias="MOMENTUM_REGIME_MA_WINDOW"
    )
    momentum_ma_short: int = Field(default=20, validation_alias="MOMENTUM_MA_SHORT")
    momentum_ma_long: int = Field(default=60, validation_alias="MOMENTUM_MA_LONG")
    momentum_window: int = Field(default=60, validation_alias="MOMENTUM_WINDOW")
    momentum_deploy_fraction: float = Field(
        default=0.95, validation_alias="MOMENTUM_DEPLOY_FRACTION"
    )

    # ---- Mainline strategy params (STRATEGY_MODE=mainline) ----
    mainline_top_k: int = Field(default=3, validation_alias="MAINLINE_TOP_K")
    mainline_min_conseq_limits: int = Field(default=2, validation_alias="MAINLINE_MIN_CONSEQ_LIMITS")
    mainline_max_prior5d_ret_pct: float = Field(default=60.0, validation_alias="MAINLINE_MAX_PRIOR5D_RET_PCT")
    mainline_min_market_cap_yi: float = Field(default=20.0, validation_alias="MAINLINE_MIN_MARKET_CAP_YI")
    mainline_max_market_cap_yi: float = Field(default=500.0, validation_alias="MAINLINE_MAX_MARKET_CAP_YI")
    mainline_stop_loss_pct: float = Field(default=7.0, validation_alias="MAINLINE_STOP_LOSS_PCT")
    mainline_trail_ma_days: int = Field(default=5, validation_alias="MAINLINE_TRAIL_MA_DAYS")
    mainline_take_profit_pct: float = Field(default=30.0, validation_alias="MAINLINE_TAKE_PROFIT_PCT")
    mainline_rebalance_every_days: int = Field(default=2, validation_alias="MAINLINE_REBALANCE_EVERY_DAYS")
    mainline_deploy_fraction: float = Field(default=0.95, validation_alias="MAINLINE_DEPLOY_FRACTION")
    mainline_max_open_gap_pct: float = Field(default=7.0, validation_alias="MAINLINE_MAX_OPEN_GAP_PCT")
    mainline_candidate_pool_size: int = Field(default=10, validation_alias="MAINLINE_CANDIDATE_POOL_SIZE")
    mainline_regime_symbol: str = Field(default="000300.SS", validation_alias="MAINLINE_REGIME_SYMBOL")
    mainline_regime_ma_window: int = Field(default=20, validation_alias="MAINLINE_REGIME_MA_WINDOW")
    mainline_regime_enabled: bool = Field(default=True, validation_alias="MAINLINE_REGIME_ENABLED")
    mainline_pullback_lookback_days: int = Field(default=10, validation_alias="MAINLINE_PULLBACK_LOOKBACK_DAYS")
    mainline_pullback_from_high_min: float = Field(default=5.0, validation_alias="MAINLINE_PULLBACK_FROM_HIGH_MIN")
    mainline_pullback_from_high_max: float = Field(default=20.0, validation_alias="MAINLINE_PULLBACK_FROM_HIGH_MAX")
    mainline_pullback_recent_chg_min: float = Field(default=-3.0, validation_alias="MAINLINE_PULLBACK_RECENT_CHG_MIN")
    mainline_pullback_recent_chg_max: float = Field(default=5.0, validation_alias="MAINLINE_PULLBACK_RECENT_CHG_MAX")
    mainline_pullback_above_ma: int = Field(default=10, validation_alias="MAINLINE_PULLBACK_ABOVE_MA")
    mainline_pullback_min_days_since_limit: int = Field(default=1, validation_alias="MAINLINE_PULLBACK_MIN_DAYS_SINCE_LIMIT")

    # ---- Rotation 策略参数 (STRATEGY_MODE=rotation) ----
    rotation_top_k: int = Field(default=3, validation_alias="ROTATION_TOP_K")
    rotation_momentum_window: int = Field(default=20, validation_alias="ROTATION_MOMENTUM_WINDOW")
    rotation_rebalance_every_days: int = Field(default=5, validation_alias="ROTATION_REBALANCE_EVERY_DAYS")
    rotation_stop_loss_pct: float = Field(default=8.0, validation_alias="ROTATION_STOP_LOSS_PCT")
    rotation_deploy_fraction: float = Field(default=0.95, validation_alias="ROTATION_DEPLOY_FRACTION")
    rotation_regime_symbol: str = Field(default="000300.SS", validation_alias="ROTATION_REGIME_SYMBOL")
    rotation_regime_ma_window: int = Field(default=60, validation_alias="ROTATION_REGIME_MA_WINDOW")
    rotation_regime_enabled: bool = Field(default=True, validation_alias="ROTATION_REGIME_ENABLED")
    rotation_require_uptrend: bool = Field(default=True, validation_alias="ROTATION_REQUIRE_UPTREND")
    rotation_uptrend_ma_window: int = Field(default=20, validation_alias="ROTATION_UPTREND_MA_WINDOW")
    rotation_live_enabled: bool = Field(default=False, validation_alias="ROTATION_LIVE_ENABLED")

    broker_mode: str = Field(default="paper", validation_alias="BROKER_MODE")
    jvquant_token: str = Field(default="", validation_alias="JVQUANT_TOKEN")
    jvquant_account: str = Field(default="", validation_alias="JVQUANT_ACCOUNT")
    jvquant_password: str = Field(default="", validation_alias="JVQUANT_PASSWORD")

    # ---- Evolution settings ----
    evolution_enabled: bool = Field(default=False, validation_alias="EVOLUTION_ENABLED")
    evolution_schedule_cron: str = Field(
        default="0 18 * * 5",
        validation_alias="EVOLUTION_SCHEDULE_CRON",
    )
    evolution_data_lookback_days: int = Field(
        default=60,
        validation_alias="EVOLUTION_DATA_LOOKBACK_DAYS",
    )
    evolution_backtest_start: str = Field(
        default="2024-01-01",
        validation_alias="EVOLUTION_BACKTEST_START",
    )
    evolution_backtest_end: str = Field(
        default="2024-12-31",
        validation_alias="EVOLUTION_BACKTEST_END",
    )
    evolution_max_mutations_per_cycle: int = Field(
        default=3,
        validation_alias="EVOLUTION_MAX_MUTATIONS_PER_CYCLE",
    )
    evolution_genome_dir: Path = Field(
        default=Path("./data/evolution"),
        validation_alias="EVOLUTION_GENOME_DIR",
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
        return m if m else self.glm_model

    def glm_completions_url(self) -> str:
        base = self.glm_base_url.strip().rstrip("/")
        path = self.glm_chat_path.strip().lstrip("/")
        return f"{base}/{path}"


def load_settings() -> Settings:
    return Settings()
