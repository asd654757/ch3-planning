#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
model="${CH3_MODEL:-qwen-vl-plus}"
base_url="${CH3_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
env_file="${CH3_ENV_FILE:-$HOME/.config/ch3-planning/dashscope.env}"
if [[ -z "${DASHSCOPE_API_KEY:-}" ]]; then
  if [[ ! -r "$env_file" ]]; then
    echo "请配置 DASHSCOPE_API_KEY 或创建私有密钥文件：$env_file" >&2; exit 1
  fi
  /root/autodl-tmp/.venvs/metaworld-lerobot/bin/python - "$env_file" <<'PYKEY'
import sys
from ch3.vlm.client import load_env_file
if not load_env_file(sys.argv[1]).get('DASHSCOPE_API_KEY'):
    raise SystemExit('私有配置文件缺少有效 DASHSCOPE_API_KEY。')
PYKEY
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
# One health check before launching 150 sessions. Never print service bodies.
/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python - "$env_file" "$model" "$base_url" <<'PYPREFLIGHT'
import sys
from ch3.vlm.client import DashScopeVLMClient
try:
    response = DashScopeVLMClient(env_path=sys.argv[1], model=sys.argv[2], base_url=sys.argv[3],
        timeout=30, max_retries=0).complete(system_prompt='Return only JSON.',
        user_prompt='Return {"ok": true}.', max_tokens=32, json_mode=True)
    if not response.content:
        raise ValueError('empty response')
except Exception as exc:
    raise SystemExit('模型服务预检失败（'+type(exc).__name__+'）；未启动实验。请核查密钥、模型权限与服务端点。')
print('模型服务预检通过。此检查调用不计入实验性能统计。')
PYPREFLIGHT
nohup setsid bash -c '
set -e
trap '''rc=$?; echo "[supervision-final] PROCESS_EXIT=$rc"''' EXIT
/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python -u scripts/compare_supervision_final.py \
 --planner openai_compatible --source "$1" --start-seed 10 --seeds 50 \
 --perturbation post_grasp_slip --env-file "$3" --model "$4" --base-url "$5" --output "$2/cases.jsonl"
/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python scripts/analyze_supervision_final.py \
 "$2/cases.jsonl" > "$2/analysis.json"
echo "[supervision-final] ALL_COMPLETED"
' bash "$source_file" "$outdir" "$env_file" "$model" "$base_url" > "$log" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > /tmp/supervision_final_pid
printf '%s\n' "$log" > /tmp/supervision_final_log
printf '%s\n' "$outdir" > /tmp/supervision_final_output
printf 'PID=%s\nLOG=%s\nOUTPUT_DIR=%s\n' "$pid" "$log" "$outdir"
