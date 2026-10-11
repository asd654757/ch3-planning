#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -z "${DASHSCOPE_API_KEY:-}" ]]; then
  echo '请在当前终端配置 DASHSCOPE_API_KEY。' >&2; exit 1
fi
source_dir=$(cat /tmp/supervision_feedback_v3_formal50_output)
source_file="$source_dir/post_grasp_slip.jsonl"
[[ -s "$source_file" ]] || { echo '缺少正式50点滑落数据。' >&2; exit 1; }
if [[ -s /tmp/supervision_final_pid ]] && kill -0 "$(cat /tmp/supervision_final_pid)" 2>/dev/null; then
  echo '已有收官实验运行，请勿重复启动。' >&2; exit 1
fi
stamp=$(date +%Y%m%d_%H%M%S)
log="logs/supervision_final_${stamp}.log"
outdir="data/collections/supervision_final_${stamp}"
mkdir -p logs "$outdir"
git rev-parse HEAD > "$outdir/launch_commit.txt"
# Verify main implementation still matches frozen V3.
/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python - <<'PYFREEZE'
import hashlib,json
from pathlib import Path
c=json.loads(Path('config/benchmarks/supervision_feedback_v3_frozen_50.json').read_text())
for name, expected in c['sha256'].items():
    if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
        raise SystemExit('Frozen source changed: '+name)
PYFREEZE
nohup bash -c '
set -e
/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python -u scripts/compare_supervision_final.py \
 --planner qwen --source "$1" --start-seed 10 --seeds 50 \
 --perturbation post_grasp_slip --output "$2/cases.jsonl"
/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python scripts/analyze_supervision_final.py \
 "$2/cases.jsonl" > "$2/analysis.json"
echo "[supervision-final] ALL_COMPLETED"
' bash "$source_file" "$outdir" > "$log" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > /tmp/supervision_final_pid
printf '%s\n' "$log" > /tmp/supervision_final_log
printf '%s\n' "$outdir" > /tmp/supervision_final_output
printf 'PID=%s\nLOG=%s\nOUTPUT_DIR=%s\n' "$pid" "$log" "$outdir"
