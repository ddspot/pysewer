#!/usr/bin/env bash
# managed by hpc-env v0.1.0 — edit in hpc-env/templates/, not here
set -euo pipefail

# Detect whether this script is sourced (so we can return instead of exiting)
_is_sourced() {
  [[ "${BASH_SOURCE[0]}" != "${0}" ]]
}

_die() {
  local code="$1"; shift || true
  if [[ $# -gt 0 ]]; then
    echo "$@" >&2
  fi
  if _is_sourced; then
    return "${code}"
  else
    exit "${code}"
  fi
}

# Install pip dependencies embedded in an explicit lock file.
# Lines look like:  # pip <pkg> @ <url>#sha256=<hash>
_install_pip_from_explicit() {
  local prefix="$1" lockfile="$2"
  local pip_urls
  pip_urls="$(sed -n 's/^# pip [^ ]* @ //p' "${lockfile}")"
  if [[ -z "${pip_urls}" ]]; then
    return 0
  fi
  local n
  n="$(printf '%s\n' "${pip_urls}" | wc -l | tr -d ' ')"
  echo "[bootstrap] installing ${n} pip dependencies from ${lockfile}"
  printf '%s\n' "${pip_urls}" | "${prefix}/bin/pip" install --no-deps -q -r /dev/stdin
}

# -----------------------------------------------------------------------------
# bootstrap_env.sh
#
# Usage:
#   source bin/bootstrap_env.sh <env_name> [env_prefix] [environment_yml] [lock_file]
#
# Examples:
#   source bin/bootstrap_env.sh uwblocks /work/$USER/conda_envs/uwblocks ./environment.yml
#   source bin/bootstrap_env.sh uwblocks /work/$USER/conda_envs/uwblocks ./environment.yml ./conda-lock.yml
#   FORCE_REBUILD=1 source bin/bootstrap_env.sh uwblocks ... ./environment.yml
#   HPC_ENV_SKIP_UV=1 source bin/bootstrap_env.sh ...   # skip uv pip install step
#
# What it does:
#   - Forces conda envs/pkgs + mamba root into /work
#   - Initializes conda for non-interactive shells
#   - Activates env by absolute prefix (HPC-robust)
#   - Rebuilds the env when FORCE_REBUILD=1
#   - Prefers a lock file when present for deterministic rebuilds
#   - Falls back to environment.yml when no lock file is available
#   - Installs the project's Python deps via uv (from pyproject.toml) unless skipped
# -----------------------------------------------------------------------------

ENV_NAME="${1:-}"
ENV_PREFIX="${2:-}"
ENV_YML="${3:-}"
LOCK_FILE="${4:-}"
FORCE_REBUILD="${FORCE_REBUILD:-0}"
HPC_ENV_SKIP_UV="${HPC_ENV_SKIP_UV:-0}"

if [[ -z "${ENV_NAME}" ]]; then
  echo "[ERROR] Missing env name."
  echo "Usage: source $0 <env_name> [env_prefix] [environment_yml] [lock_file]"
  _die 2
fi

# Default prefixes
: "${ENV_PREFIX:=/work/${USER}/conda_envs/${ENV_NAME}}"

# Default lockfile if not explicitly provided
if [[ -z "${LOCK_FILE}" ]]; then
  if [[ -n "${ENV_YML}" ]]; then
    ENV_DIR="$(cd "$(dirname "${ENV_YML}")" && pwd)"
    if [[ -f "${ENV_DIR}/conda-linux-64.lock" ]]; then
      LOCK_FILE="${ENV_DIR}/conda-linux-64.lock"
    elif [[ -f "${ENV_DIR}/conda-lock.yml" ]]; then
      LOCK_FILE="${ENV_DIR}/conda-lock.yml"
    fi
  fi
fi

# Hard guardrails: keep everything off $HOME
export CONDA_AUTO_ACTIVATE_BASE=false
if [[ -d "/work/${USER}" ]]; then
  export CONDA_ENVS_PATH="/work/${USER}/conda_envs"
  export CONDA_PKGS_DIRS="/work/${USER}/conda_pkgs"
  export MAMBA_ROOT_PREFIX="/work/${USER}/.mamba"
  mkdir -p "${CONDA_ENVS_PATH}" "${CONDA_PKGS_DIRS}" "${MAMBA_ROOT_PREFIX}"
  # uv's cache (under $HOME by default) and the env target on /work are on
  # different filesystems on most HPC sites, which trips uv's hardlink path.
  # Force copy mode to silence the warning and avoid surprise fallbacks.
  : "${UV_LINK_MODE:=copy}"
  export UV_LINK_MODE
fi
export PYTHONNOUSERSITE=1
unset PYTHONHOME

# Conda/Mamba init for non-interactive shells
if command -v mamba >/dev/null 2>&1; then
  CONDA_CMD="mamba"
  echo "[bootstrap] Using mamba"
elif command -v conda >/dev/null 2>&1; then
  CONDA_CMD="conda"
  echo "[bootstrap] Using conda"
else
  echo "[ERROR] Neither mamba nor conda found."
  _die 3
fi

# Get base path and initialize shell integration
CONDA_BASE="$(${CONDA_CMD} info --base 2>/dev/null || true)"
if [[ -n "${CONDA_BASE}" && -f "${CONDA_BASE}/etc/profile.d/conda.sh" ]]; then
  # shellcheck disable=SC1090
  source "${CONDA_BASE}/etc/profile.d/conda.sh"
  eval "$(conda shell.bash hook)"
fi

if command -v mamba >/dev/null 2>&1 && [[ -n "${CONDA_BASE}" && -f "${CONDA_BASE}/etc/profile.d/mamba.sh" ]]; then
  # shellcheck disable=SC1090
  source "${CONDA_BASE}/etc/profile.d/mamba.sh"
fi

echo "[bootstrap] ENV_NAME      : ${ENV_NAME}"
echo "[bootstrap] ENV_PREFIX    : ${ENV_PREFIX}"
echo "[bootstrap] CONDA_ENVS_PATH: ${CONDA_ENVS_PATH:-<unset>}"
echo "[bootstrap] CONDA_PKGS_DIRS: ${CONDA_PKGS_DIRS:-<unset>}"
echo "[bootstrap] MAMBA_ROOT_PREFIX: ${MAMBA_ROOT_PREFIX:-<unset>}"
echo "[bootstrap] ENV_YML       : ${ENV_YML:-<none>}"
echo "[bootstrap] LOCK_FILE     : ${LOCK_FILE:-<none>}"
echo "[bootstrap] FORCE_REBUILD : ${FORCE_REBUILD}"
echo "[bootstrap] HPC_ENV_SKIP_UV: ${HPC_ENV_SKIP_UV}"

# Optionally force a rebuild of the env
if [[ "${FORCE_REBUILD}" == "1" && -d "${ENV_PREFIX}" ]]; then
  echo "[bootstrap] FORCE_REBUILD=1 -> removing existing env at ${ENV_PREFIX}"
  rm -rf "${ENV_PREFIX}"
fi

# Create env if missing. Prefer lock file, fall back to environment.yml.
if [[ ! -d "${ENV_PREFIX}" ]]; then
  if [[ -n "${LOCK_FILE}" && -f "${LOCK_FILE}" ]]; then
    echo "[bootstrap] env missing -> creating from lock file ${LOCK_FILE}"
    case "${LOCK_FILE}" in
      *.yml|*.yaml)
        if command -v conda-lock >/dev/null 2>&1 && conda-lock --version >/dev/null 2>&1; then
          conda-lock install --conda "${CONDA_CMD}" -p "${ENV_PREFIX}" "${LOCK_FILE}"
        else
          LOCK_DIR="$(cd "$(dirname "${LOCK_FILE}")" && pwd)"
          EXPLICIT_LOCK="${LOCK_DIR}/conda-linux-64.lock"
          if [[ -f "${EXPLICIT_LOCK}" ]]; then
            echo "[bootstrap] conda-lock CLI not found, falling back to ${EXPLICIT_LOCK}"
            LOCK_FILE="${EXPLICIT_LOCK}"
            if command -v mamba >/dev/null 2>&1; then
              mamba create -y -p "${ENV_PREFIX}" --file "${LOCK_FILE}"
            else
              conda create -y -p "${ENV_PREFIX}" --file "${LOCK_FILE}"
            fi
            _install_pip_from_explicit "${ENV_PREFIX}" "${LOCK_FILE}"
          else
            echo "[ERROR] conda-lock CLI not found and no explicit lock file (conda-linux-64.lock) available."
            echo "        Run 'make lock' locally to generate both conda-lock.yml and conda-linux-64.lock,"
            echo "        then sync the files to the cluster."
            _die 4
          fi
        fi
        ;;
      *)
        if command -v mamba >/dev/null 2>&1; then
          mamba create -y -p "${ENV_PREFIX}" --file "${LOCK_FILE}"
        else
          conda create -y -p "${ENV_PREFIX}" --file "${LOCK_FILE}"
        fi
        _install_pip_from_explicit "${ENV_PREFIX}" "${LOCK_FILE}"
        ;;
    esac
  elif [[ -n "${ENV_YML}" && -f "${ENV_YML}" ]]; then
    echo "[bootstrap] env missing -> creating from environment file ${ENV_YML}"
    ${CONDA_CMD} env create -y -p "${ENV_PREFIX}" -f "${ENV_YML}"
  else
    echo "[ERROR] Env not found at ${ENV_PREFIX}"
    echo "        Provide an environment.yml or lock file, or create the env first."
    _die 4
  fi
else
  echo "[bootstrap] env exists -> activating"
fi

# Activate by absolute prefix (prevents lookup surprises)
echo "[bootstrap] Activating: conda activate ${ENV_PREFIX}"
conda activate "${ENV_PREFIX}"

# Verify activation worked
ACTIVATED_ENV="${CONDA_PREFIX:-}"
echo "[bootstrap] CONDA_PREFIX: ${ACTIVATED_ENV}"

if [[ -z "${ACTIVATED_ENV}" ]]; then
  echo "[ERROR] CONDA_PREFIX is empty - activation failed!"
  echo "        Available envs:"
  conda env list
  _die 6
fi

REAL_ENV_PREFIX="$(readlink -f "${ENV_PREFIX}" 2>/dev/null || echo "${ENV_PREFIX}")"
REAL_ACTIVATED_ENV="$(readlink -f "${ACTIVATED_ENV}" 2>/dev/null || echo "${ACTIVATED_ENV}")"

if [[ "${REAL_ACTIVATED_ENV}" != "${REAL_ENV_PREFIX}" ]]; then
  echo "[ERROR] Wrong environment activated!"
  echo "        Expected: ${REAL_ENV_PREFIX}"
  echo "        Got:      ${REAL_ACTIVATED_ENV}"
  echo "        Available envs:"
  conda env list
  _die 6
fi

echo "[bootstrap] ✓ Environment activated correctly"

# Force env bin to the front of PATH and strip stale HOME-based env bins for this env
ENV_BIN="${ENV_PREFIX}/bin"
if [[ -d "${ENV_BIN}" ]]; then
  CLEAN_PATH="$PATH"
  CLEAN_PATH="$(printf '%s' "${CLEAN_PATH}" | awk -v RS=: -v ORS=: -v envname="/${ENV_NAME}/bin" '
    {
      if ($0 == "") next
      if ((index($0, "/home/") > 0 || index($0, "/gpfs1/") > 0) && index($0, envname) > 0) next
      print
    }
  ' | sed 's/:$//')"
  export PATH="${ENV_BIN}:${CLEAN_PATH}"
  hash -r
  echo "[bootstrap] Prepended ${ENV_BIN} to PATH"
else
  echo "[ERROR] Environment bin directory not found: ${ENV_BIN}"
  _die 7
fi

# Report python/pip resolution after activation (must come from the env)
PY="$(command -v python || true)"
PY_REAL="$(readlink -f "${PY}" 2>/dev/null || echo "${PY}")"
PIP_PATH="$(command -v pip || true)"
PIP_REAL="$(readlink -f "${PIP_PATH}" 2>/dev/null || echo "${PIP_PATH}")"
PY_VERSION="$(python --version 2>&1)"

echo "[bootstrap] python: ${PY}"
echo "[bootstrap] python (real): ${PY_REAL}"
echo "[bootstrap] version: ${PY_VERSION}"
echo "[bootstrap] pip: ${PIP_PATH}"
echo "[bootstrap] pip (real): ${PIP_REAL}"

# Sanity check: allow for multiple mountpoints (/work vs /gpfs1/work) and python symlinks
REAL_ENV_BIN="${REAL_ENV_PREFIX}/bin"
REAL_PY_DIR="$(dirname "${PY_REAL}")"
REAL_PIP_DIR="$(dirname "${PIP_REAL}")"

echo "[bootstrap] env prefix (real): ${REAL_ENV_PREFIX}"

case "${PY_REAL}" in
  /home/*|/gpfs1/*/home/*)
    echo "[ERROR] Activated python resolves under HOME. This defeats the purpose."
    _die 5
    ;;
  *)
    :
    ;;
esac

if [[ "${REAL_PY_DIR}" == "${REAL_ENV_BIN}" ]]; then
  echo "[bootstrap] ✓ Python is from the env prefix (correct)"
else
  echo "[ERROR] Wrong python resolved: ${PY_REAL}"
  echo "        Expected python under: ${REAL_ENV_BIN}"
  echo "        This can happen if the bootstrap was executed (subshell) instead of sourced,"
  echo "        or if PATH is being overwritten later in the job script."
  echo "        Fix: source bin/bootstrap_env.sh ... and avoid resetting PATH after activation."
  _die 5
fi

if [[ "${REAL_PIP_DIR}" == "${REAL_ENV_BIN}" ]]; then
  echo "[bootstrap] ✓ Pip is from the env prefix (correct)"
else
  echo "[ERROR] Wrong pip resolved: ${PIP_REAL}"
  echo "        Expected pip under: ${REAL_ENV_BIN}"
  _die 8
fi

# Export helpers for callers that want the env executables explicitly
export BOOTSTRAP_PYTHON="${CONDA_PREFIX}/bin/python"
export BOOTSTRAP_PIP="${CONDA_PREFIX}/bin/pip"

# uv project install (mamba+uv split): conda env owns native stack,
# uv installs the project Python deps from pyproject.toml.
if [[ "${HPC_ENV_SKIP_UV}" != "1" ]]; then
  PROJECT_ROOT="${HPC_ENV_PROJECT_ROOT:-$(pwd)}"
  if [[ -n "${ENV_YML}" && -f "${ENV_YML}" ]]; then
    PROJECT_ROOT="$(cd "$(dirname "${ENV_YML}")" && pwd)"
  fi
  if [[ -f "${PROJECT_ROOT}/pyproject.toml" ]]; then
    UV_BIN="${CONDA_PREFIX}/bin/uv"
    if [[ -x "${UV_BIN}" ]]; then
      EXTRAS="${HPC_ENV_UV_EXTRAS:-dev}"
      if [[ -n "${EXTRAS}" ]]; then
        TARGET="${PROJECT_ROOT}[${EXTRAS}]"
      else
        TARGET="${PROJECT_ROOT}"
      fi
      echo "[bootstrap] uv pip install --python ${CONDA_PREFIX}/bin/python -e ${TARGET}"
      "${UV_BIN}" pip install --python "${CONDA_PREFIX}/bin/python" -e "${TARGET}"
    else
      echo "[bootstrap] uv not found in env (${UV_BIN}); skipping project install."
      echo "            Add 'uv' to environment.yml or set HPC_ENV_SKIP_UV=1 to silence."
    fi
  fi
fi
