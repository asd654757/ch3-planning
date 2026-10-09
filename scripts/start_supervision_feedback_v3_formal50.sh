#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -z "${DASHSCOPE_API_KEY:-}" ]]; then
  echo '请在当前终端配置 DASHSCOPE_API_KEY。' >&2
  exit 1
fi
# Fail before spending API calls if a frozen implementation file changed.
/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python - <<'PYFREEZE'
import json, hashlib
from pathlib import Path
config = json.loads(Path('config/benchmarks/supervision_feedback_v3_frozen_50.json').read_text())
for name, expected in config['sha256'].items():
    if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
        raise SystemExit('Frozen source changed: ' + name)
PYFREEZE
pidfile=/tmp/supervision_feedback_v3_formal50_pid
if [[ -s "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
  echo '已有正式实验进程，请勿重复启动。' >&2
  exit 1
fi
stamp=$(date +%Y%m%d_%H%M%S)
log="logs/supervision_feedback_v3_formal50_${stamp}.log"
outdir="data/collections/supervision_feedback_v3_formal50_${stamp}"
mkdir -p logs "$outdir"
cp config/benchmarks/supervision_feedback_v3_frozen_50.json "$outdir/frozen_protocol.json"
git rev-parse HEAD > "$outdir/launch_commit.txt"
nohup bash -c '
set -e
for perturbation in place_timeout post_grasp_slip; do
  /root/autodl-tmp/.venvs/metaworld-lerobot/bin/python -u \
    scripts/compare_metaworld_supervision.py --planner qwen --seeds 50 --start-seed 10 \
    --perturbation "$perturbation" --output "$1/$perturbation.jsonl"
done
echo "[feedback-v3-formal50] ALL_COMPLETED"
' bash "$outdir" > "$log" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > /tmp/supervision_feedback_v3_formal50_pid
printf '%s\n' "$log" > /tmp/supervision_feedback_v3_formal50_log
printf '%s\n' "$outdir" > /tmp/supervision_feedback_v3_formal50_output
printf 'PID=%s\nLOG=%s\nOUTPUT_DIR=%s\n' "$pid" "$log" "$outdir"
