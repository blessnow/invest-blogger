# AGENTS.md

Paper trading simulation system with GLM-5 LLM strategy and real market prices (Yahoo Finance + akshare).

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copy `example.env` to `.env` and set `ANTHROPIC_AUTH_TOKEN` (required for `STRATEGY_MODE=llm`).

**PYTHONPATH**: Railway/Nixpacks only runs `pip install -r requirements.txt`, not an editable install. `scripts/start_railway.sh` sets `PYTHONPATH=src/` to load from source. For local runs without pip install, do the same.

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

**Built-in scheduler (推荐，跨平台，可直接部署到 Railway):**
```bash
python3 -m invest_system.scheduler         # 常驻进程：4 节点 + 16:30 缓存清理
# 或：invest-scheduler （setup.py 控制台脚本）
```

Procfile (Railway / Heroku 类平台):
```text
web:    python -m streamlit run src/invest_system/dashboard.py --server.port $PORT --server.address 0.0.0.0 --server.headless true
worker: python -m invest_system.scheduler
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
  - `llm_strategy.py` - GLM-5 API integration
  - `data_feed.py` - Yahoo Finance price fetching
  - `market_scanner.py` - akshare-based CN market scanning (free selection mode)
  - `dashboard.py` - Streamlit UI
  - `assistant/` - Intraday market commentary generation

## Key Configuration

All config via `.env` (pydantic-settings). Critical vars:

- `ANTHROPIC_AUTH_TOKEN` - Required for LLM strategy
- `STRATEGY_MODE` - `llm` (default) or `buy_hold` (no API, for testing)
- `SELECTION_MODE` - `fixed` (UNIVERSE only) or `free` (model picks from market scan)
- `UNIVERSE` - Comma-separated Yahoo symbols (e.g., `600519.SS,300750.SZ`)
- `START_DATE` / `END_DATE` - Backtest period

Yahoo symbol format: Shanghai `.SS`, Shenzhen `.SZ` (e.g., `600519.SS`).

## Live Trading (实盘交易)

支持模拟盘和实盘切换，通过 `BROKER_MODE` 配置：

- `paper` (默认) - 模拟盘，仅在内存中更新持仓
- `jvquant` - jvQuant 实盘（需注册 jvQuant 并开通东方财富账户）

**配置示例 (.env)：**
```bash
BROKER_MODE=jvquant
JVQUANT_TOKEN=your_token        # jvQuant API token
JVQUANT_ACCOUNT=12位资金账号      # 券商资金账号
JVQUANT_PASSWORD=交易密码         # 资金交易密码
```

**jvQuant 说明：**
- 个人账户仅支持东方财富
- 机构账户无券商限制
- 文档：http://jvquant.com/wiki.html

**Broker 架构：**
- `src/invest_system/broker/__init__.py` - 抽象层 + 工厂函数
- `src/invest_system/broker/paper.py` - 模拟盘执行器
- `src/invest_system/broker/jvquant.py` - jvQuant 实盘执行器

## Output

- `data/` - Cached OHLCV (`.pkl`), equity curves (`*_equity.csv`), transactions (`*_transactions.csv`)
- `data/articles/` - Generated market commentary (markdown, was `artifacts/assistant/`)
- `data/docs/` - One-off conversion artifacts (markdown)
- `seed_data/` - Cold-start seed shipped in image (copied to `DATA_DIR` if it's empty on first boot)
- `logs/` - LaunchAgent logs

## Deploy to Railway

1. **Volume**：Add a Volume in your service, mount path 例如 `/data`。
2. **Variables** 至少设置：
   - `DEEPSEEK_API_KEY=...`
   - `DATA_DIR=/data`
   - `ASSISTANT_ARTIFACTS_DIR=/data/articles`
   - `LIVE_PORTFOLIO_STATE_PATH=/data/live_portfolio_state.json`
   - `DASHBOARD_AUTH_ENABLED=true`
   - `DASHBOARD_USERS=admin:your-strong-pass`（可逗号分隔多账户：`u1:p1,u2:p2`）
3. 第一次启动：`/data` 是空的，程序会自动从镜像里 `seed_data/` 拷贝当前持仓 JSON、equity CSV、文章目录到 Volume；之后所有读写都直接落到 Volume，下次重启不丢数据。
4. **进程**：`Procfile` 已声明
   - `web` → Streamlit 看板（含登录闸）
   - `worker` → `invest_system.scheduler`（4 个盘中节点 + 16:30 缓存清理）
5. 想跳过种子数据：`SEED_DATA_ENABLED=false`。

## Notes

- No test suite. Verify by running `invest-sim` with `STRATEGY_MODE=buy_hold`.
- `data/`, `artifacts/`, `logs/` are gitignored; `seed_data/` ships with repo for cold-start.
- Market scanner uses akshare for CN A-share real-time rankings (requires network, optional proxy via `MARKET_SCAN_HTTP_PROXY`).
- DeepSeek API: default endpoint `https://api.deepseek.com/chat/completions` (no `/v1` prefix).
- All config via pydantic-settings (`src/invest_system/config.py`). Env vars override `.env` file values.
