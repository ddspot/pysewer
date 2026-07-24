#!/usr/bin/env bash
# managed by hpc-env v0.1.0 — edit in hpc-env/templates/, not here
# Thin shim used by SLURM job scripts to keep the legacy `slurm/bootstrap_env.sh`
# entry point working. Sources the canonical implementation in bin/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BIN_BOOTSTRAP="${PROJECT_ROOT}/bin/bootstrap_env.sh"

if [[ ! -f "${BIN_BOOTSTRAP}" ]]; then
  echo "[ERROR] Missing bootstrap implementation: ${BIN_BOOTSTRAP}" >&2
  exit 2
fi

# shellcheck disable=SC1090
source "${BIN_BOOTSTRAP}" "$@"
