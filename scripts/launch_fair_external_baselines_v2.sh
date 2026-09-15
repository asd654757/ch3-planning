#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="/root/autodl-tmp/.venvs/metaworld-lerobot/bin/python"
TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
OUTPUT="${REPO_ROOT}/data/collections/fair_external_baselines_v2_${TIMESTAMP}.jsonl"
LOG="/tmp/fair_external_v2_formal.log"
PID_FILE="/tmp/fair_external_v2_formal.pid"
OUTPUT_FILE="/tmp/fair_external_v2_formal.output"

cd "${REPO_ROOT}"

setsid env \
  PYTHONPATH="${REPO_ROOT}" \
  MUJOCO_GL=egl \
  "${PYTHON}" scripts/fair_external_baselines_v2.py \
  --output "${OUTPUT}" \
  > "${LOG}" 2>&1 < /dev/null &

PID=$!
echo "${PID}" > "${PID_FILE}"
echo "${LOG}" > /tmp/fair_external_v2_formal_log
echo "${OUTPUT}" > "${OUTPUT_FILE}"

echo "PID=${PID}"
echo "LOG=${LOG}"
echo "OUTPUT=${OUTPUT}"
