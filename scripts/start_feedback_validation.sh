#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python=/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python
env_file="$HOME/.config/ch3-planning/dashscope.env"
if [[ -s /tmp/feedback_validation_pid ]] && kill -0 "$(cat /tmp/feedback_validation_pid)" 2>/dev/null; then
    echo 'Validation already running'; exit 1
fi
[[ -s "$env_file" ]] || { echo 'Missing private API environment file'; exit 1; }
export PYTHONPATH="$PWD" MUJOCO_GL=egl
"$python" -m py_compile scripts/flexible_fixture_pilot.py scripts/persistent_reference_sequence.py scripts/analyze_feedback_validation.py
stamp=$(date +%Y%m%d_%H%M%S)
root="data/collections/feedback_v10_validation_${stamp}"
log="logs/feedback_v10_validation_${stamp}.log"
mkdir -p "$root" logs
"$python" - "$root" <<'PY'
import hashlib,json,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1])
files=['ch3/repair/flexible_fixture_planner.py','scripts/flexible_fixture_pilot.py',
       'scripts/persistent_reference_sequence.py','ch3/execution/multiobject_scene.py','ch3/vlm/client.py']
p={'version':'feedback_v10_heldout_layout_validation','seeds':list(range(600,620)),
   'conditions':['medium','hard'],'expected_episodes':120,
   'model':'deepseek-v4.1-flash','enable_thinking':False,'max_tokens':4096,
   'max_noninitial_requests':2,'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
   'source_sha256':{f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files},
   'primary_comparison':'compact versus legacy feedback recovery tokens and calls, with all task outcomes',
   'secondary_comparison':'compact versus strong direct replanning, reported separately',
   'scope':'new layouts, same restricted two-object task families; no broad generalization claim',
   'rules':['No feedback edits during run. No winner-based exclusions or reruns.',
            'Include API, parse, validation and physical failures.',
            'Costs exclude shared initial generation; do not compare raw asymmetric API totals.',
            'Success ties do not establish noninferiority. No latency superiority claim.',
            'Repeated prompt structures limit independent-task statistical interpretation.']}
(root/'validation_protocol.json').write_text(json.dumps(p,indent=2))
PY
setsid -f bash -c '
echo $$ >/tmp/feedback_validation_pid
for difficulty in medium hard; do
    "$1" -u scripts/flexible_fixture_pilot.py --env-file "$2" --model deepseek-v4.1-flash \
      --disable-thinking --max-tokens 4096 --seeds $(seq 600 619) --difficulty "$difficulty" \
      --execution-contract --compare-feedback-formats --output-dir "$3/$difficulty"
    rc=$?; echo "[feedback-validation] condition=$difficulty PROCESS_EXIT=$rc"
    if [ "$rc" -ne 0 ]; then exit "$rc"; fi
done
"$1" scripts/analyze_feedback_validation.py "$3" > "$3/validation_analysis.json"
rc=$?; echo "[feedback-validation] ANALYSIS_EXIT=$rc"
if [ "$rc" -eq 0 ]; then echo "[feedback-validation] ALL_COMPLETED"; fi
' bash "$python" "$env_file" "$root" >"$log" 2>&1 </dev/null
echo "$log" >/tmp/feedback_validation_log
echo "$root" >/tmp/feedback_validation_output
echo "LOG=$log OUTPUT=$root"
