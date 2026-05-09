#!/usr/bin/env bash
# 把本地 .env 中的关键变量同步到 Railway 当前 linked service。
# 单一真理源：本地 .env；改完密码或任何配置后跑一次本脚本即可。

set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v railway >/dev/null; then
  echo "需要先安装 railway CLI: https://docs.railway.app/develop/cli" >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  echo "未找到 .env" >&2
  exit 1
fi

# 这些 key 会推到 Railway。其余路径类（DATA_DIR / ASSISTANT_ARTIFACTS_DIR / LIVE_PORTFOLIO_STATE_PATH）
# 由本脚本固定指向 Volume 挂载点，避免本地 ./data 被错误带上去。
KEYS=(
  DEEPSEEK_API_KEY
  DEEPSEEK_BASE_URL
  DEEPSEEK_MODEL
  DEEPSEEK_THINKING_TYPE
  DEEPSEEK_REASONING_EFFORT
  INITIAL_CAPITAL
  SELECTION_MODE
  CALENDAR_SYMBOL
  REFERENCE_BENCHMARKS
  UNIVERSE
  START_DATE
  END_DATE
  STRATEGY_MODE
  REBALANCE_EVERY_DAYS
  MAX_POSITION_FRACTION
  LOT_SIZE
  COMMISSION_RATE
  INTRADAY_ASSISTANT
  ASSISTANT_RSS_URLS
  ASSISTANT_GATHER_URL
  ASSISTANT_MODEL
  ASSISTANT_TEMPERATURE
  ASSISTANT_HTTP_TIMEOUT_SEC
  ASSISTANT_MAX_RSS_ITEMS_TOTAL
  ASSISTANT_MAX_BUNDLE_CHARS
  ASSISTANT_LLM_TIMEOUT_SEC
  DASHBOARD_AUTH_ENABLED
  DASHBOARD_USERS
)

ARGS=()
for key in "${KEYS[@]}"; do
  # 取 .env 里 KEY=... 这一行（首个匹配，去除注释行），允许值含 = 和空格
  line=$(grep -E "^${key}=" .env | grep -v '^[[:space:]]*#' | head -n1 || true)
  if [[ -z "${line}" ]]; then
    continue
  fi
  value="${line#${key}=}"
  # Railway CLI 不允许空值，跳过
  if [[ -z "${value}" ]]; then
    continue
  fi
  ARGS+=("--set" "${key}=${value}")
done

# Volume 路径固定写死（Volume 挂载在 /data）
ARGS+=("--set" "DATA_DIR=/data")
ARGS+=("--set" "ASSISTANT_ARTIFACTS_DIR=/data/articles")
ARGS+=("--set" "LIVE_PORTFOLIO_STATE_PATH=/data/live_portfolio_state.json")
ARGS+=("--set" "SEED_DATA_ENABLED=true")
ARGS+=("--set" "SEED_DATA_DIR=./seed_data")

if (( ${#ARGS[@]} == 0 )); then
  echo "没有找到可同步的变量" >&2
  exit 1
fi

echo "[sync_railway_env] pushing ${#ARGS[@]} flags to Railway…"
railway variables "${ARGS[@]}" --skip-deploys
echo "[sync_railway_env] done. 重新部署: railway up 或 git push"
