# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A-share paper/real trading system powered by DeepSeek LLM for portfolio rebalancing. Supports backtesting, live intraday trading (4 phases), and a Streamlit dashboard. Deploys to Railway via single Procfile.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp example.env .env   # fill DEEPSEEK_API_KEY at minimum
export PYTHONPATH=src  # required when not using pip install -e
```

## Commands

| What | Command |
|---|---|
| Backtest | `invest-sim` or `invest-sim --prefix my_run` |
| Dashboard | `./scripts/start_dashboard.sh` |
| Scheduler daemon | `invest-scheduler` or `python3 -m invest_system.scheduler` |
| Single live phase | `python3 -m invest_system.live_phase --phase pre_open` |
| Strategy evolution | `invest-evolve` or `invest-evolve analyze` |
| Evolution status | `invest-evolve status` |
| Rollback genome | `invest-evolve rollback <genome_id>` |
| Smoke test (no API) | `STRATEGY_MODE=buy_hold invest-sim` |

Phases: `pre_open` (9:20), `open_5m` (9:35), `midday` (11:30), `close` (15:05).

## Architecture

**Data flow**: Yahoo Finance (yfinance) + akshare → pickle cache → multi-index DataFrame panel → LLM prompt → JSON actions → Broker execution.

**Entry points** (`pyproject.toml` console_scripts):
- `cli.py` → `invest-sim` (backtest)
- `live_phase.py` → `invest-live-phase` (single phase)
- `scheduler.py` → `invest-scheduler` (APScheduler daemon, 4 cron jobs Mon-Fri)
- `dashboard.py` → Streamlit UI

**Core modules** (`src/invest_system/`):
- `config.py` — pydantic-settings `Settings`, all config from `.env`/env vars. Single object passed everywhere.
- `engine.py` — Backtest loop: iterates trading days, builds prompts, calls LLM, applies buy/sell with position sizing and lot-size rounding.
- `portfolio.py` — `Portfolio` dataclass: cash, positions, transactions, avg-cost tracking, T+1 restriction, fee calculation.
- `llm_strategy.py` — DeepSeek API calls. Two system prompts (fixed/free selection mode). Three modes: async, sync JSON, text completion.
- `data_feed.py` — Yahoo Finance OHLCV fetching, pickle caching, multi-index panel construction.
- `market_scanner.py` — akshare-based A-share scanning for `free` selection mode (momentum + liquidity ranking, proxy support).
- `broker/` — `Broker` ABC + `create_broker()` factory. `paper.py` (in-memory via Portfolio), `jvquant.py` (real trading via jvQuant OpenAPI).

**Assistant sub-package** (`assistant/`): 4-phase intraday market commentary (RSS + news search → LLM articles → markdown files).

**Evolution sub-package** (`evolution/`): Self-evolving strategy engine. Cycle: analyze performance → LLM proposes mutations → backtest validate → apply if improved. Mutations can change config params, system prompts, scoring weights, or inject custom scoring code. Genomes are versioned JSON with lineage tracking. Enable via `EVOLUTION_ENABLED=true`.

**Key modes** (all via `.env`):
- `STRATEGY_MODE`: `llm` (DeepSeek decisions) | `buy_hold` (equal-weight, no API)
- `SELECTION_MODE`: `fixed` (trade only UNIVERSE) | `free` (LLM picks from market scan)
- `BROKER_MODE`: `paper` (simulation) | `jvquant` (real trading)

## Conventions

- Yahoo symbol format: Shanghai `600519.SS`, Shenzhen `300750.SZ`
- Timezone: `Asia/Shanghai` throughout
- Lot size: 100 shares (A-share standard), configurable via `LOT_SIZE`
- T+1 enforced by default (`T_PLUS_1_ENABLED=true`)
- Commission: 0.00025 single-side approximation
- DeepSeek endpoint: `https://api.deepseek.com/chat/completions` (no `/v1` prefix)
- `data/` is gitignored runtime state; `seed_data/` is committed for cold-start
- No test suite — verify with `STRATEGY_MODE=buy_hold invest-sim`
- No linter/formatter configured
