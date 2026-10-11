#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -s /tmp/supervision_stress_pid ]] && kill -0 "$(cat /tmp/supervision_stress_pid)" 2>/dev/null; then
 echo 'Stress benchmark already running'; exit 1
fi
python=/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python
envfile="${CH3_ENV_FILE:-$HOME/.config/ch3-planning/dashscope.env}"
model="${CH3_MODEL:-deepseek-v4.1-flash}"
source="$(cat /tmp/supervision_feedback_v3_formal50_output)/post_grasp_slip.jsonl"
stamp=$(date +%Y%m%d_%H%M%S)
out="data/collections/supervision_stress_${stamp}"
log="logs/supervision_stress_${stamp}.log"
mkdir -p "$out" logs
git rev-parse HEAD > "$out/launch_commit.txt"
nohup setsid bash -c '
set -e
worker_exit() { rc=$?; echo "[supervision-stress] PROCESS_EXIT=$rc"; }
trap worker_exit EXIT
for profile in repeat_slip repeat_slip_place_timeout; do
 "$1" -u scripts/compare_supervision_final.py --planner openai_compatible \
 --model "$5" --env-file "$4" --source "$2" --start-seed 10 --seeds 50 \
 --perturbation post_grasp_slip --stress-profile "$profile" --output "$3/$profile.jsonl"
 "$1" scripts/analyze_supervision_final.py "$3/$profile.jsonl" > "$3/${profile}_analysis.json"
done
echo "[supervision-stress] ALL_COMPLETED"
' bash "$python" "$source" "$out" "$envfile" "$model" > "$log" 2>&1 < /dev/null &
printf '%s\n' "$!" > /tmp/supervision_stress_pid
printf '%s\n' "$log" > /tmp/supervision_stress_log
printf '%s\n' "$out" > /tmp/supervision_stress_output
printf 'PID=%s\nLOG=%s\nOUTPUT=%s\n' "$!" "$log" "$out"
