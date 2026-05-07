# AGENTS.md

Paper trading simulation system with DeepSeek LLM strategy and real market prices (Yahoo Finance + akshare).

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copy `example.env` to `.env` and set `DEEPSEEK_API_KEY` (required for `STRATEGY_MODE=llm`).

## Commands

**Run backtest simulation:**
```bash
invest-sim                    # Uses dates from .env (START_DATE, END_DATE)
invest-sim --prefix my_run    # Custom output prefix
```

**Start Streamlit dashboard:**
```bash
./scripts/start_dashboard.sh
# or: python3 -m streamlit run src/invest_system/dashboard.py
```

**Live intraday phase (macOS LaunchAgent):**
```bash
./scripts/install-macos-agents.sh   # Install 4 daily phases + dashboard
./scripts/uninstall-macos-agents.sh # Remove agents
```

Manual single phase:
```bash
python3 -m invest_system.live_phase --phase pre_open   # 9:20
python3 -m invest_system.live_phase --phase open_5m    # 9:35
python3 -m invest_system.live_phase --phase midday     # 11:30
python3 -m invest_system.live_phase --phase close      # 15:05
```

## Architecture

- `src/invest_system/` - Main package
  - `cli.py` - Entry point for `invest-sim` command
  - `live_phase.py` - Entry point for intraday scheduled runs
  - `engine.py` - Core simulation loop (backtest)
  - `llm_strategy.py` - DeepSeek API integration
  - `data_feed.py` - Yahoo Finance price fetching
  - `market_scanner.py` - akshare-based CN market scanning (free selection mode)
  - `dashboard.py` - Streamlit UI
  - `assistant/` - Intraday market commentary generation

## Key Configuration

All config via `.env` (pydantic-settings). Critical vars:

- `DEEPSEEK_API_KEY` - Required for LLM strategy
- `STRATEGY_MODE` - `llm` (default) or `buy_hold` (no API, for testing)
- `SELECTION_MODE` - `fixed` (UNIVERSE only) or `free` (model picks from market scan)
- `UNIVERSE` - Comma-separated Yahoo symbols (e.g., `600519.SS,300750.SZ`)
- `START_DATE` / `END_DATE` - Backtest period

Yahoo symbol format: Shanghai `.SS`, Shenzhen `.SZ` (e.g., `600519.SS`).

## Output

- `data/` - Cached OHLCV (`.pkl`), equity curves (`*_equity.csv`), transactions (`*_transactions.csv`)
- `artifacts/assistant/` - Generated market commentary (markdown)
- `logs/` - LaunchAgent logs

## Notes

- No test suite. Verify by running `invest-sim` with `STRATEGY_MODE=buy_hold`.
- `data/`, `artifacts/`, `logs/` are gitignored.
- Market scanner uses akshare for CN A-share real-time rankings (requires network, optional proxy via `MARKET_SCAN_HTTP_PROXY`).
- DeepSeek API: default endpoint `https://api.deepseek.com/chat/completions` (no `/v1` prefix).
