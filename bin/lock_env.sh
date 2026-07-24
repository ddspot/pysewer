#!/usr/bin/env bash
# managed by hpc-env v0.1.0 — edit in hpc-env/templates/, not here
set -euo pipefail

ENV_FILE="${1:-environment.yml}"
EXPLICIT_LOCK="conda-linux-64.lock"

if ! command -v conda-lock >/dev/null 2>&1; then
  echo "[ERROR] conda-lock not found"
  echo "Install it with: pip install conda-lock   (or: mamba install -c conda-forge conda-lock)"
  exit 1
fi

echo "[lock] generating unified lockfile from ${ENV_FILE}"
conda-lock -f "${ENV_FILE}" -p linux-64
echo "[lock] done -> conda-lock.yml"

# Render an explicit lock file for HPC where conda-lock may not be installed.
echo "[lock] rendering explicit lock file for linux-64"
conda-lock render -p linux-64 -k explicit \
  --filename-template "${EXPLICIT_LOCK}" conda-lock.yml
echo "[lock] done -> ${EXPLICIT_LOCK}"
echo ""
echo "[lock] Generated files:"
echo "  conda-lock.yml         (unified, needs conda-lock to install)"
echo "  ${EXPLICIT_LOCK}   (explicit, works with plain mamba/conda)"
echo ""
echo "Commit both files so HPC builds are reproducible without conda-lock."
