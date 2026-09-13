#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python"
SCENARIOS="data/scenarios/multiskill_tasks_v1.jsonl"
SOURCE="data/collections/repair_pressure_multiskill_formal_v1_20260913_140057.jsonl"
ENV_FILE="/root/autodl-tmp/metaworld-smolvla/qwen-dialogue-control/.env"
PRESSURES=(duplicate_skill_after_prefix unknown_object_after_generic_prefix invalid_action_after_prefix)
TS="${MULTISKILL_ABLATION_TS:-$(date +%Y%m%d_%H%M%S)}"

run_arm() {
  local mode="$1"
  shift
  local output="data/collections/repair_pressure_multiskill_ablation_${mode}_${TS}.jsonl"
  local log="logs/repair_pressure_multiskill_ablation_${mode}_${TS}.log"
  echo "[ablation] mode=${mode} output=${output} log=${log}" | tee "${log}"
  PYTHONPATH=. "$PYTHON_BIN" scripts/repair_pressure.py \
    --scenarios "$SCENARIOS" \
    --source-collection "$SOURCE" \
    --pressure-types "${PRESSURES[@]}" \
    --repair-groups ROUTED \
    --routed-ablation "$mode" \
    --benchmark "multiskill_ablation_v1_${mode}" \
    --seeds 3 --seed-offset 0 \
    --temperature 0.7 --repair-temperature 0.3 \
    --client real --env-file "$ENV_FILE" \
    --output "$output" >> "$log" 2>&1
}

run_arm no_truncation
run_arm no_r2
run_arm no_state_r2 --difficulties multiskill_pick_place
run_arm no_fallback --difficulties multiskill_pick_place

echo "{\"completed_at_utc\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"timestamp\": \"${TS}\"}"
