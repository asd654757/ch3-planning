#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -z "${DASHSCOPE_API_KEY:-}" ]]; then
  echo 'DASHSCOPE_API_KEY 未配置；请在当前终端配置后重试。' >&2
  exit 1
fi
stamp=$(date +%Y%m%d_%H%M%S)
mkdir -p logs data/collections
log="logs/supervision_paired_pilot_${stamp}.log"
out="data/collections/supervision_paired_pilot_${stamp}.jsonl"
nohup /root/autodl-tmp/.venvs/metaworld-lerobot/bin/python -u \
  scripts/compare_metaworld_supervision.py --planner qwen --seeds 10 \
  --output "$out" > "$log" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > /tmp/supervision_paired_pilot_pid
printf '%s\n' "$log" > /tmp/supervision_paired_pilot_log
printf '%s\n' "$out" > /tmp/supervision_paired_pilot_output
printf 'PID=%s\nLOG=%s\nOUTPUT=%s\n' "$pid" "$log" "$out"
