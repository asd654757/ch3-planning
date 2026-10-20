#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python=/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python
env_file="$HOME/.config/ch3-planning/dashscope.env"
[[ -s "$env_file" ]] || { echo 'Missing private environment file'; exit 1; }
if [[ -s /tmp/core_recovery_pilot_pid ]] && kill -0 "$(cat /tmp/core_recovery_pilot_pid)" 2>/dev/null; then
  echo 'Pilot already running'; exit 1
fi
export PYTHONPATH="$PWD" MUJOCO_GL=egl
stamp=$(date +%Y%m%d_%H%M%S)
root="data/collections/core_recovery_pilot_$stamp"
log="logs/core_recovery_pilot_$stamp.log"
mkdir -p "$root" logs
"$python" - "$root" <<'PY'
import hashlib,json,subprocess,sys
from pathlib import Path
files=['ch3/repair/flexible_fixture_planner.py','scripts/flexible_fixture_pilot.py',
       'scripts/persistent_reference_sequence.py','ch3/vlm/client.py']
p=dict(protocol='shared_recovery_evidence_v1',seeds=list(range(790,795)),
    conditions=['medium','hard'],expected_episodes=20,
    model='deepseek-v4.1-flash',max_tokens=4096,enable_thinking=False,
    git_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
    source_sha256={f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files},
    rules=['No result-based seed exclusion or prompt edits during run.',
           'Same raw evidence, gates, controller and noninitial call budget.',
           'No history necessity claim; hard includes controlled user goal update.',
           'Formal expansion is not conditional on a favorable method gap.',
           'Retain API, parsing, validation and physical failures.'],formal_ready=False)
(Path(sys.argv[1])/'pilot_manifest.json').write_text(json.dumps(p,indent=2))
PY
setsid -f bash -c '
echo $$ > /tmp/core_recovery_pilot_pid
for difficulty in medium hard; do
  "$1" -u scripts/flexible_fixture_pilot.py --env-file "$2" --disable-thinking \
    --max-tokens 4096 --seeds 790 791 792 793 794 --difficulty "$difficulty" \
    --execution-contract --compare-compact-direct --output-dir "$3/$difficulty"
  rc=$?; echo "[core-pilot] condition=$difficulty PROCESS_EXIT=$rc"
  if [ "$rc" -ne 0 ]; then exit "$rc"; fi
done
echo "[core-pilot] ALL_COMPLETED"
' bash "$python" "$env_file" "$root" >"$log" 2>&1 </dev/null
echo "$log" > /tmp/core_recovery_pilot_log
echo "$root" > /tmp/core_recovery_pilot_output
echo "LOG=$log OUTPUT=$root"
