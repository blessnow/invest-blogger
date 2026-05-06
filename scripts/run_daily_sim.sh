#!/usr/bin/env bash
# 可选：一段历史区间的「离线回测」滚动窗口（非盘中定时；盘中定时请用 install-macos-agents.sh + live_phase）。
#
# crontab 示例：
#   30 15 * * 1-5 cd /path/to/一体化 && ./scripts/run_daily_sim.sh >> logs/daily_sim.log 2>&1
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
DAYS="${ROLLING_DAYS:-60}"
PREFIX="${SIM_PREFIX:-daily_live}"
export START_DATE="$(python3 -c "from datetime import date, timedelta; print((date.today()-timedelta(days=int('$DAYS'))).isoformat())")"
export END_DATE="$(python3 -c "from datetime import date; print(date.today().isoformat())")"
exec invest-sim --prefix "$PREFIX"
