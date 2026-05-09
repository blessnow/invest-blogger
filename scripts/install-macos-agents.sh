#!/usr/bin/env bash
# 安装 LaunchAgents：
#   · A 股四个节点（Asia/Shanghai）：盘前 9:20、开盘+5分 9:35、午间 11:30、收盘后 15:05 → python3 -m invest_system.live_phase
#   · 每天 16:30 跑一次缓存清理（兜底，防 ohlcv/prices pkl 越积越多）
#   · 登录后常驻 Streamlit 看板
# 用法：./scripts/install-macos-agents.sh
# 卸载：./scripts/uninstall-macos-agents.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_DIR="${HOME}/Library/LaunchAgents"
mkdir -p "$AGENT_DIR" "$ROOT/logs"

UID_OUT="$(id -u)"

bootout_plist() {
  local p="$1"
  [[ -f "$p" ]] || return 0
  launchctl bootout "gui/${UID_OUT}" "$p" 2>/dev/null || true
  launchctl unload "$p" 2>/dev/null || true
}

bootstrap() {
  launchctl bootstrap "gui/${UID_OUT}" "$1"
}

/usr/bin/python3 - <<PY
import os, sys, plistlib
root = "${ROOT}"
agent_dir = os.path.expanduser("${AGENT_DIR}")
os.makedirs(agent_dir, exist_ok=True)
port = os.environ.get("STREAMLIT_PORT", "8501")

# 去掉旧的「每日一次回测」任务（若存在）
legacy = os.path.join(agent_dir, "com.invest-system.daily-sim.plist")

jobs = [
    ("com.invest-system.live-preopen", "pre_open", 9, 20),
    ("com.invest-system.live-open5m", "open_5m", 9, 35),
    ("com.invest-system.live-midday", "midday", 11, 30),
    ("com.invest-system.live-close", "close", 15, 5),
]

path_env = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

for label, phase, hour, minute in jobs:
    intervals = [{"Weekday": wd, "Hour": hour, "Minute": minute} for wd in range(1, 6)]
    log = os.path.join(root, "logs", f"live_{phase}.log")
    data = {
        "Label": label,
        "WorkingDirectory": root,
        "ProgramArguments": [
            "/bin/bash",
            "-lc",
            f'cd "{root}" && exec python3 -m invest_system.live_phase --phase {phase}',
        ],
        "StartCalendarInterval": intervals,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": {
            "PATH": path_env,
            "TZ": "Asia/Shanghai",
        },
    }
    outp = os.path.join(agent_dir, f"{label}.plist")
    with open(outp, "wb") as f:
        plistlib.dump(data, f)

cache_label = "com.invest-system.cache-janitor"
cache_log = os.path.join(root, "logs", "cache_janitor.log")
cache_data = {
    "Label": cache_label,
    "WorkingDirectory": root,
    "ProgramArguments": [
        "/bin/bash",
        "-lc",
        f'cd "{root}" && exec python3 -m invest_system.cache_janitor --data-dir "{os.path.join(root, "data")}"',
    ],
    "StartCalendarInterval": [{"Hour": 16, "Minute": 30}],
    "StandardOutPath": cache_log,
    "StandardErrorPath": cache_log,
    "EnvironmentVariables": {"PATH": path_env, "TZ": "Asia/Shanghai"},
}
with open(os.path.join(agent_dir, f"{cache_label}.plist"), "wb") as f:
    plistlib.dump(cache_data, f)

dash_label = "com.invest-system.dashboard"
dash_cmd = (
    f'cd "{root}" && exec python3 -m streamlit run src/invest_system/dashboard.py '
    f"--server.port {port} --server.headless true --server.address 127.0.0.1"
)
dash = {
    "Label": dash_label,
    "WorkingDirectory": root,
    "ProgramArguments": ["/bin/bash", "-lc", dash_cmd],
    "RunAtLoad": True,
    "KeepAlive": True,
    "StandardOutPath": os.path.join(root, "logs", "dashboard_stdout.log"),
    "StandardErrorPath": os.path.join(root, "logs", "dashboard_stderr.log"),
    "EnvironmentVariables": {"PATH": path_env},
}
with open(os.path.join(agent_dir, f"{dash_label}.plist"), "wb") as f:
    plistlib.dump(dash, f)

print("WROTE", len(jobs) + 2, "plists under", agent_dir)
PY

# 卸载旧 daily-sim + 加载新任务
bootout_plist "${AGENT_DIR}/com.invest-system.daily-sim.plist"
rm -f "${AGENT_DIR}/com.invest-system.daily-sim.plist"

for p in \
  "${AGENT_DIR}/com.invest-system.live-preopen.plist" \
  "${AGENT_DIR}/com.invest-system.live-open5m.plist" \
  "${AGENT_DIR}/com.invest-system.live-midday.plist" \
  "${AGENT_DIR}/com.invest-system.live-close.plist" \
  "${AGENT_DIR}/com.invest-system.cache-janitor.plist" \
  "${AGENT_DIR}/com.invest-system.dashboard.plist"; do
  bootout_plist "$p"
done

bootstrap "${AGENT_DIR}/com.invest-system.live-preopen.plist"
bootstrap "${AGENT_DIR}/com.invest-system.live-open5m.plist"
bootstrap "${AGENT_DIR}/com.invest-system.live-midday.plist"
bootstrap "${AGENT_DIR}/com.invest-system.live-close.plist"
bootstrap "${AGENT_DIR}/com.invest-system.cache-janitor.plist"
bootstrap "${AGENT_DIR}/com.invest-system.dashboard.plist"

echo "已安装并加载（触发时区 TZ=Asia/Shanghai）："
echo "  · pre_open      周一至周五 09:20"
echo "  · open_5m       周一至周五 09:35"
echo "  · midday        周一至周五 11:30"
echo "  · close         周一至周五 15:05"
echo "  · cache-janitor 每天 16:30（兜底清理 ohlcv/prices pkl）"
echo "  · dashboard     登录后 → http://127.0.0.1:${STREAMLIT_PORT:-8501}"
echo "日志：$ROOT/logs/live_*.log、cache_janitor.log、dashboard_*.log"
