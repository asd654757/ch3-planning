#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -z "${DASHSCOPE_API_KEY:-}" ]]; then
  echo '请先在当前终端配置 DASHSCOPE_API_KEY。' >&2
  exit 1
fi
stamp=$(date +%Y%m%d_%H%M%S)
log="logs/supervision_harder_${stamp}.log"
outdir="data/collections/supervision_harder_${stamp}"
mkdir -p logs "$outdir"
nohup bash -c '
set -e
for perturbation in place_timeout post_grasp_slip; do
  /root/autodl-tmp/.venvs/metaworld-lerobot/bin/python -u \
    scripts/compare_metaworld_supervision.py --planner qwen --seeds 10 \
    --perturbation "$perturbation" --output "$1/$perturbation.jsonl"
done
echo "[harder-pilot] ALL_COMPLETED"
' bash "$outdir" > "$log" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > /tmp/supervision_harder_pilot_pid
printf '%s\n' "$log" > /tmp/supervision_harder_pilot_log
printf '%s\n' "$outdir" > /tmp/supervision_harder_pilot_output
printf 'PID=%s\nLOG=%s\nOUTPUT_DIR=%s\n' "$pid" "$log" "$outdir"
