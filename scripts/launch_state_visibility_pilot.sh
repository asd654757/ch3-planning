#!/usr/bin/env bash
set -euo pipefail
cd /root/autodl-tmp/ch3-planning
export PYTHONPATH=/root/autodl-tmp/ch3-planning
export MUJOCO_GL=egl
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUT="/root/autodl-tmp/ch3-planning/data/collections/state_visibility_pilot_${TIMESTAMP}.jsonl"
LOG="/root/autodl-tmp/ch3-planning/logs/state_visibility_pilot_${TIMESTAMP}.log"
mkdir -p "$(dirname "$OUT")" "$(dirname "$LOG")"
setsid /root/autodl-tmp/.venvs/metaworld-lerobot/bin/python scripts/state_visibility_pilot.py \
  --manifest data/collections/state_visibility_pilot_manifest_20260915.json \
  --output "$OUT" \
  >"$LOG" 2>&1 </dev/null &
PID=$!
echo "$PID" > /tmp/state_visibility_pilot_pid
echo "$OUT" > /tmp/state_visibility_pilot_output
echo "$LOG" > /tmp/state_visibility_pilot_log
echo "PID=$PID"
echo "OUTPUT=$OUT"
echo "LOG=$LOG"
