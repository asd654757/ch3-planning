#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python=/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python
env_file="$HOME/.config/ch3-planning/dashscope.env"
[[ -s "$env_file" ]] || { echo 'Missing private API environment'; exit 1; }
if [[ -s /tmp/occlusion_model_pilot_pid ]] && kill -0 "$(cat /tmp/occlusion_model_pilot_pid)" 2>/dev/null; then
 echo 'Pilot already running'; exit 1
fi
export PYTHONPATH="$PWD" MUJOCO_GL=egl
stamp=$(date +%Y%m%d_%H%M%S)
out="data/collections/occlusion_model_pilot_${stamp}"
log="logs/occlusion_model_pilot_${stamp}.log"
mkdir -p logs
setsid -f bash -c '
 echo $$ >/tmp/occlusion_model_pilot_pid
 "$1" -u scripts/flexible_fixture_pilot.py --env-file "$2" --output-dir "$3" \
   --model deepseek-v4.1-flash --disable-thinking --max-tokens 4096 \
   --seeds 760 761 762 763 764 --difficulty medium --execution-contract \
   --compare-compact-direct --occlusion-observation
 rc=$?; echo "[occlusion-model-pilot] PROCESS_EXIT=$rc"; exit "$rc"
' bash "$python" "$env_file" "$out" >"$log" 2>&1 </dev/null
echo "$log" >/tmp/occlusion_model_pilot_log
echo "$out" >/tmp/occlusion_model_pilot_output
echo "OUTPUT=$out LOG=$log"
