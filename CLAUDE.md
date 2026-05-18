# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A-share paper/real trading system powered by DeepSeek LLM for portfolio rebalancing. Supports backtesting, live intraday trading (4 phases), self-evolving strategies, and a Streamlit dashboard. Single Procfile deploys to Railway with persistent volumes.

## Setup & Development

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp example.env .env   # fill DEEPSEEK_API_KEY (can be empty for STRATEGY_MODE=buy_hold)
export PYTHONPATH=src  # CRITICAL: required for local runs without pip install -e
```

**Verify setup** (no API calls):
```bash
STRATEGY_MODE=buy_hold invest-sim
```

**Required dependencies**:
- Python 3.10+
- Main packages: pydantic, yfinance, akshare, pandas, streamlit, apscheduler
- Ensure akshare can access Chinese market data (may require proxy configuration)
- Set `ANTHROPIC_AUTH_TOKEN` in .env for GLM-5 API access

## Commands

| What | Command |
|---|---|
| Backtest (date range in .env) | `invest-sim` |
| Backtest (custom prefix) | `invest-sim --prefix my_run` |
| Backtest (specific dates) | `START_DATE=2024-01-01 END_DATE=2024-01-31 invest-sim` |
| Dashboard | `./scripts/start_dashboard.sh` (or `streamlit run src/invest_system/dashboard.py`) |
| Scheduler daemon | `invest-scheduler` or `python3 -m invest_system.scheduler` |
| Single live phase | `python3 -m invest_system.live_phase --phase pre_open \|open_5m\|midday\|close` |
| Evolution analyze | `invest-evolve analyze` |
| Evolution status | `invest-evolve status` |
| Rollback genome | `invest-evolve rollback <genome_id>` |
| Data cache cleanup | `python3 -m invest_system.cache_janitor` |
| Transaction timestamp fix | `python3 scripts/backfill_tx_timestamps.py` |

**Development utilities**:
```bash
# Check market data availability
python3 -c "from invest_system.data_feed import DataFeed; df = DataFeed().fetch(['600519.SS'], start='2024-01-01', end='2024-01-02'); print(df)"

# Test market scanner
python3 -c "from invest_system.market_scanner import MarketScanner; scanner = MarketScanner(); print(scanner.scan_top_n(10))"
```

**Live phases** run at market times: `pre_open` (9:20), `open_5m` (9:35), `midday` (11:30), `close` (15:05). Scheduler daemon (APScheduler) auto-runs all 4 phases Mon-Fri + cache cleanup at 16:30.

## Architecture

**Data pipeline**: Yahoo Finance (yfinance) + akshare → pickle cache → multi-index DataFrame → LLM strategy prompt → JSON actions → Broker execution.

**Entry points** (console_scripts in `pyproject.toml`):
- `cli.py` → `invest-sim` (backtest engine)
- `live_phase.py` → intraday execution of a single market phase
- `scheduler.py` → APScheduler daemon (4 phases + cache cleanup)
- `dashboard.py` → Streamlit UI with optional auth
- `evolution/cli.py` → `invest-evolve` (strategy mutation/validation)

**Core modules** (`src/invest_system/`):
- `config.py` — Pydantic-settings `Settings`, all config from `.env` + env vars (env vars override .env)
- `engine.py` — Backtest loop: iterate trading days, build LLM prompts, execute buy/sell with position sizing, T+1 logic
- `portfolio.py` — `Portfolio` dataclass: cash, positions, transactions, avg-cost tracking, T+1 restriction, commission calc
- `llm_strategy.py` — GLM-5 API layer (async/sync JSON/text). System prompts differ between `fixed` and `free` selection modes
- `data_feed.py` — Yahoo Finance OHLCV fetching, pickle caching, multi-index DataFrame panel construction
- `market_scanner.py` — akshare real-time A-share rankings (momentum+liquidity) for `free` selection mode; includes proxy support
- `broker/` — Abstract `Broker` + factory. `paper.py` (in-memory simulation), `jvquant.py` (live jvQuant API, needs credentials)
- `assistant/` — 4-phase intraday market commentary (RSS feeds + news search → LLM summarization → markdown articles)
- `evolution/` — Self-evolving strategy engine:
  - `genome.py` — Strategy genome (config + system prompt + scoring weights)
  - `evolver.py` — Main evolution orchestrator
  - `analyzer.py` — Performance analysis and scoring
  - `validator.py` — Mutation validation and testing
  - `applier.py` — Apply validated mutations to strategy
  - `cli.py` — Command-line interface
- `engine_hooks.py` — Extensible hooks (pre/post trade, end-of-day) for custom logic injection
- `auth.py` — Streamlit session auth (user/pass or token-based)
- `cache_janitor.py` — Prune old OHLCV/price caches (configurable by age)
- `market_context.py` — Optional HTTP service to fetch sentiment, news summaries, or external signals for LLM context
- `bootstrap.py` — Cold-start logic: seed historical data, copy from `seed_data/` if `DATA_DIR` empty
- `transactions_io.py` — Load/save transaction ledger from CSV/JSON
- `stock_names.py` — A-share name lookup (akshare cache)
- `symbols.py` — Ticker/symbol utilities
- `tx_backfill.py` — Fill missing transaction timestamps from market data

**Evolution engine capabilities**:
- Mutates: system prompts, config params, scoring weights, custom scoring code
- Genomes stored as JSON with parent lineage and performance metrics
- Backtests mutations and only applies if performance improves
- Enable with `EVOLUTION_ENABLED=true` in `.env`

**Key modes** (via `.env`):
- `STRATEGY_MODE`: `llm` (DeepSeek) | `buy_hold` (equal-weight, no API)
- `SELECTION_MODE`: `fixed` (trade only UNIVERSE) | `free` (LLM picks any A-share; requires akshare scan)
- `BROKER_MODE`: `paper` (simulation) | `jvquant` (live trading; requires jvQuant account)
- `EVOLUTION_ENABLED`: `true` (auto-mutate strategy) | `false`

## Development Notes

**Data persistence**:
- `data/` — Runtime cache (gitignored):
  - `*_equity.csv` — Daily net worth curve
  - `*_transactions.csv` — Trade log
  - `ohlcv_*.pkl`, `prices_*.pkl` — Cached market data
  - `articles/` — Generated market commentary
- `seed_data/` — Committed initial data (copied to `DATA_DIR` on first boot if empty):
  - `articles/` — Pre-generated commentary for cold-start
  - `live_portfolio_state.json` — Initial positions

**Market data flow**:
1. Yahoo Finance (yfinance) → fetches OHLCV data
2. akshare → provides A-share rankings for free mode
3. Pickle cache → stores fetched data to avoid redundant API calls
4. Multi-index DataFrame → organizes data for efficient access
5. LLM prompt → contains portfolio state and market data
6. JSON actions → buy/sell decisions from LLM
7. Broker execution → executes trades (paper or live)

**Configuration patterns**:
- All config via pydantic-settings in `config.py`
- Environment variables override `.env` file values
- Critical: `PYTHONPATH=src` required for local development
- Market data cache lifetime controlled by `CACHE_PRUNE_MAX_AGE_DAYS`

**Real trading** (jvQuant):
- Register at jvQuant, open East Money account
- Personal accounts: East Money only; institutional: any broker
- Set `BROKER_MODE=jvquant`, `JVQUANT_TOKEN`, `JVQUANT_ACCOUNT`, `JVQUANT_PASSWORD` in `.env`

**Market scanning** (free mode):
- Uses akshare for real-time momentum/liquidity rankings
- Requires network; optionally set `MARKET_SCAN_HTTP_PROXY` if behind proxy
- Caches results (fallback if scan fails); age limit configurable via `MARKET_SCAN_CACHE_MAX_AGE_MIN`

## Railway Deployment

1. Create a Volume; mount at `/data`
2. Set env vars:
   - `DEEPSEEK_API_KEY` (required if `STRATEGY_MODE=llm`)
   - `DATA_DIR=/data`
   - `ASSISTANT_ARTIFACTS_DIR=/data/articles`
   - `LIVE_PORTFOLIO_STATE_PATH=/data/live_portfolio_state.json`
   - `DASHBOARD_AUTH_ENABLED=true`
   - `DASHBOARD_USERS=admin:password` (multi-user: `u1:p1,u2:p2`)
3. First boot: if `/data` is empty, app seeds it from `seed_data/` (portfolio, articles, etc.)
4. Procfile runs `bash scripts/start_railway.sh` (sets PYTHONPATH, starts Streamlit + scheduler)
5. Disable seeding: `SEED_DATA_ENABLED=false`

## Conventions

- Yahoo symbol format: Shanghai `.SS` (e.g., `600519.SS`), Shenzhen `.SZ` (e.g., `300750.SZ`)
- Timezone: `Asia/Shanghai` throughout
- Lot size: 100 shares (A-share standard), configurable via `LOT_SIZE`
- T+1 enforced by default (`T_PLUS_1_ENABLED=true`); set to `false` to bypass in backtest
- Commission: single-side 0.00025 (approximation; can be overridden)
- DeepSeek: endpoint `https://api.deepseek.com/chat/completions` (no `/v1` prefix)
- No test suite — verify via `STRATEGY_MODE=buy_hold invest-sim` (pure buy-and-hold, no LLM)
- No linter/formatter configured
- Data directories: `data/` runtime, `seed_data/` committed
- All config via `.env` and environment variables

**Environment variable priorities** (highest to lowest):
1. Command-line exports (e.g., `export DEEPSEEK_API_KEY=xyz`)
2. System environment variables
3. `.env` file values
4. Default values in `config.py`

**Error handling patterns**:
- Market fetch failures → use cached data if available
- API timeouts → retry with exponential backoff
- LLM failures → fall back to buy-hold mode
- Invalid symbols → skip and log error
