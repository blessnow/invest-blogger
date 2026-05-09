#!/usr/bin/env bash
# Railway 单 service 启动脚本：后台拉起 scheduler，前台跑 Streamlit 看板。
# 容器收到 SIGTERM 时，trap 杀掉后台 scheduler，再让 streamlit 自然退出。

set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${PORT:-8501}"

echo "[start_railway] launching scheduler in background…"
python -m invest_system.scheduler &
SCHED_PID=$!

cleanup() {
  echo "[start_railway] shutting down scheduler pid=$SCHED_PID"
  kill "$SCHED_PID" 2>/dev/null || true
  wait "$SCHED_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[start_railway] launching streamlit on :$PORT (scheduler pid=$SCHED_PID)"
exec python -m streamlit run src/invest_system/dashboard.py \
  --server.port "$PORT" \
  --server.address 0.0.0.0 \
  --server.headless true \
  --browser.gatherUsageStats false
