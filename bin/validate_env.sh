#!/usr/bin/env bash
# managed by hpc-env v0.1.0 — edit in hpc-env/templates/, not here
set -euo pipefail

ENV_FILE="${1:-environment.yml}"
ENV_NAME="${2:-pysewer}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[ERROR] environment file not found: ${ENV_FILE}" >&2
  exit 1
fi

if command -v mamba >/dev/null 2>&1; then
  CONDA_CMD="mamba"
elif command -v conda >/dev/null 2>&1; then
  CONDA_CMD="conda"
else
  echo "[ERROR] neither mamba nor conda was found" >&2
  exit 1
fi

TMP_ROOT="${TMPDIR:-/tmp}/${ENV_NAME}-conda-validate"
mkdir -p "${TMP_ROOT}/pkgs" "${TMP_ROOT}/envs" "${TMP_ROOT}/cache"

export CONDA_PKGS_DIRS="${TMP_ROOT}/pkgs"
export CONDA_ENVS_PATH="${TMP_ROOT}/envs"
export XDG_CACHE_HOME="${TMP_ROOT}/cache"

echo "[validate] checking ${ENV_FILE} with ${CONDA_CMD}"
TMP_PREFIX="${TMPDIR:-/tmp}/${ENV_NAME}-validate-prefix"
"${CONDA_CMD}" env create --dry-run -p "${TMP_PREFIX}" -f "${ENV_FILE}" >/dev/null || {
  echo "[validate] solver check failed" >&2
  echo "[validate] if this is a network-restricted session, rerun where conda-forge metadata is reachable or cached" >&2
  exit 1
}
echo "[validate] ${ENV_FILE} is parseable and solvable"
