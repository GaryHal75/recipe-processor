#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_PATH="${REMOTE_PATH:-example-remote:recipes_raw}"
RAW_DIR="${RAW_DIR:-/srv/recipes/raw}"
STRUCTURED_DIR="${STRUCTURED_DIR:-/srv/recipes/structured}"
PIPELINE_SCRIPT="${REPO_DIR}/scripts/recipe_pipeline.py"
PYTHON_BIN="${PYTHON_BIN:-${REPO_DIR}/.venv/bin/python}"
API_ENV_FILE="${API_ENV_FILE:-}"
API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:8787}"

LOCAL_CONFIG="${REPO_DIR}/scripts/recipe_alt_refresh.local.sh"
if [[ -f "${LOCAL_CONFIG}" ]]; then
  # shellcheck disable=SC1090
  source "${LOCAL_CONFIG}"
fi

STATE_PATH="${STRUCTURED_DIR}/.pipeline_state.json"

mkdir -p "${RAW_DIR}" "${STRUCTURED_DIR}"

if [[ -f "${API_ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${API_ENV_FILE}"
fi

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] sync starting"
/usr/bin/rclone sync "${REMOTE_PATH}" "${RAW_DIR}"

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] ingest starting"
"${PYTHON_BIN}" "${PIPELINE_SCRIPT}" \
  --source "${RAW_DIR}" \
  --out "${STRUCTURED_DIR}" \
  --state "${STATE_PATH}"

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] reload starting"
if [[ -n "${RECIPE_API_KEY:-}" ]]; then
  /usr/bin/curl -fsS -X POST -H "X-API-Key: ${RECIPE_API_KEY}" "${API_BASE_URL}/reload" >/dev/null
else
  /usr/bin/curl -fsS -X POST "${API_BASE_URL}/reload" >/dev/null
fi

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] refresh complete"
