#!/usr/bin/env bash
set -euo pipefail
AGENT_DIR="${HOME}/Library/LaunchAgents"
UID_OUT="$(id -u)"
LABELS=(
  com.invest-system.live-preopen
  com.invest-system.live-open5m
  com.invest-system.live-midday
  com.invest-system.live-close
  com.invest-system.dashboard
  com.invest-system.daily-sim
)
for label in "${LABELS[@]}"; do
  p="${AGENT_DIR}/${label}.plist"
  [[ -f "$p" ]] && launchctl bootout "gui/${UID_OUT}" "$p" 2>/dev/null || true
  [[ -f "$p" ]] && launchctl unload "$p" 2>/dev/null || true
  rm -f "$p"
done
echo "已卸载上述 LaunchAgents（含旧 daily-sim）。"