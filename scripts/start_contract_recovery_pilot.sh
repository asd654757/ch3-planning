#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -s /tmp/contract_recovery_pid ]] && kill -0 "$(cat /tmp/contract_recovery_pid)" 2>/dev/null; then echo 'Already running'; exit 1; fi
stamp=$(date +%Y%m%d_%H%M%S)
log="logs/contract_recovery_${stamp}.log"; out="data/collections/contract_recovery_${stamp}.jsonl"
mkdir -p logs data/collections
nohup setsid bash -c '
/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python -u scripts/contract_recovery_pilot.py --seeds 5 --output "$1"
rc=$?; echo "[contract] PROCESS_EXIT=$rc"; exit "$rc"
' bash "$out" > "$log" 2>&1 < /dev/null &
echo $! > /tmp/contract_recovery_pid; echo "$log" > /tmp/contract_recovery_log; echo "$out" > /tmp/contract_recovery_output
printf 'PID=%s\nLOG=%s\nOUTPUT=%s\n' "$!" "$log" "$out"
