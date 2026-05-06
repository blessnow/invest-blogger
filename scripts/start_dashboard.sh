#!/usr/bin/env bash
# 在项目根目录启动 Streamlit 看板（默认 http://127.0.0.1:8501）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT="${STREAMLIT_SERVER_PORT:-8501}"
ADDR="${STREAMLIT_SERVER_ADDRESS:-127.0.0.1}"
exec python3 -m streamlit run src/invest_system/dashboard.py \
  --server.port "$PORT" \
  --server.address "$ADDR"
