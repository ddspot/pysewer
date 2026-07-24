#!/usr/bin/env bash
# managed by hpc-env v0.1.0 — edit in hpc-env/templates/, not here
# Diagnose the active conda env: paths, key imports, uv state, lockfile freshness.
set -uo pipefail

ENV_PREFIX="${1:-${CONDA_PREFIX:-}}"
PROJECT_ROOT="${HPC_ENV_PROJECT_ROOT:-$(pwd)}"

hr() { printf '%*s\n' 70 '' | tr ' ' -; }

hr
echo "[doctor] host        : $(uname -a)"
echo "[doctor] user        : ${USER:-?}"
echo "[doctor] pwd         : $(pwd)"
echo "[doctor] project_root: ${PROJECT_ROOT}"
echo "[doctor] env_prefix  : ${ENV_PREFIX:-<unset>}"
hr

if [[ -z "${ENV_PREFIX}" ]]; then
  echo "[doctor] No active env. Activate or pass prefix as \$1."
  exit 1
fi

if [[ ! -d "${ENV_PREFIX}" ]]; then
  echo "[doctor] ENV_PREFIX does not exist: ${ENV_PREFIX}"
  exit 1
fi

PY="${ENV_PREFIX}/bin/python"
PIP="${ENV_PREFIX}/bin/pip"
UV="${ENV_PREFIX}/bin/uv"

echo "[doctor] python : ${PY} -> $("${PY}" -c 'import sys; print(sys.executable)' 2>&1)"
echo "[doctor] version: $("${PY}" --version 2>&1)"
echo "[doctor] pip    : $([[ -x "${PIP}" ]] && "${PIP}" --version 2>&1 || echo '<missing>')"
echo "[doctor] uv     : $([[ -x "${UV}" ]] && "${UV}" --version 2>&1 || echo '<missing>')"
hr

echo "[doctor] PATH (env-bin first?):"
echo "${PATH}" | tr ':' '\n' | head -8 | sed 's/^/  /'
hr

# Imports to check: configurable via env vars so projects without the
# default geospatial stack can substitute their own. Space-separated.
DOCTOR_REQUIRED="${HPC_ENV_DOCTOR_REQUIRED:-numpy pandas pyarrow geopandas rasterio fiona pyproj shapely networkx}"
DOCTOR_OPTIONAL="${HPC_ENV_DOCTOR_OPTIONAL:-xarray rioxarray netcdf4 h5netcdf}"
DOCTOR_LABEL="${HPC_ENV_DOCTOR_LABEL:-native geospatial imports}"

echo "[doctor] ${DOCTOR_LABEL}:"
HPC_ENV_DOCTOR_REQUIRED="${DOCTOR_REQUIRED}" \
HPC_ENV_DOCTOR_OPTIONAL="${DOCTOR_OPTIONAL}" \
"${PY}" -s -c "
import importlib, os, sys
required = os.environ['HPC_ENV_DOCTOR_REQUIRED'].split()
optional = os.environ['HPC_ENV_DOCTOR_OPTIONAL'].split()
bad = []
for m in required:
    try:
        importlib.import_module(m); print(f'  ok   {m}')
    except Exception as e:
        bad.append(m); print(f'  FAIL {m}: {e}')
for m in optional:
    try:
        importlib.import_module(m); print(f'  ok   {m} (optional)')
    except Exception:
        print(f'  --   {m} (optional, not installed)')
sys.exit(1 if bad else 0)
" || echo "[doctor] one or more required imports failed"
hr

echo "[doctor] lockfile freshness:"
for f in environment.yml conda-lock.yml conda-linux-64.lock pyproject.toml uv.lock; do
  p="${PROJECT_ROOT}/${f}"
  if [[ -f "${p}" ]]; then
    # date -r is the only mtime accessor portable across BSD (macOS) and GNU/GPFS coreutils
    # without falling through to verbose `stat` dumps.
    mtime="$(date -r "${p}" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo '?')"
    printf '  %-25s %s\n' "${f}" "${mtime}"
  fi
done
hr
echo "[doctor] done"
