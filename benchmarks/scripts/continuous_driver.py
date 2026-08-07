# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""Continuous benchmark driver: published pysewer (v0.1.20) vs current main
across all cases — repo datasets, reviewer case, Wasweta II and the Elan
French dataset, plus any locally generated cases dropped into
work/benchmarks/cases_generated/ (e.g. open-data cases; not committed).

Runs sequentially (fast cases first), survives per-case failures, and keeps
a rolling log + report under work/benchmarks/results/. Intended to run in
the background:

    python benchmarks/scripts/continuous_driver.py
"""

import datetime
import subprocess
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
RESULTS = REPO_ROOT / "work" / "benchmarks" / "results"
CASE_DIR = SCRIPTS.parent / "cases"
GENERATED = REPO_ROOT / "work" / "benchmarks" / "cases_generated"

# static cases, fast -> slow; reviewer_full (17k buildings) queued last
STATIC_ORDER = [
    "small_test",
    "example_case",
    "wasweta_ii",
    "reviewer_reduced",
    "elan_french",
    "reviewer_full",
]


def log(msg):
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    with open(RESULTS / "driver_log.txt", "a") as fh:
        fh.write(line + "\n")


def run_step(description, argv):
    log(f"START {description}")
    try:
        proc = subprocess.run(argv, cwd=REPO_ROOT, capture_output=True, text=True)
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-8:]
        for line in tail:
            log(f"    {line}")
        log(f"{'OK   ' if proc.returncode == 0 else 'FAIL '}{description}")
        return proc.returncode == 0
    except Exception:
        log(f"ERROR {description}\n{traceback.format_exc()}")
        return False


def main():
    log("=== continuous benchmark driver started ===")
    py = sys.executable

    run_step("stage datasets", [py, str(SCRIPTS / "stage_datasets.py")])

    for case in STATIC_ORDER:
        case_path = CASE_DIR / f"{case}.yaml"
        if not case_path.exists():
            log(f"SKIP {case}: no case file")
            continue
        run_step(
            f"compare {case}",
            [py, str(SCRIPTS / "compare_versions.py"), str(case_path)],
        )

    generated = sorted(GENERATED.glob("*.yaml"))
    if generated:
        log(f"generated cases: {[c.stem for c in generated]}")
    for case_path in generated:
        run_step(
            f"compare {case_path.stem}",
            [py, str(SCRIPTS / "compare_versions.py"), str(case_path)],
        )

    log("=== driver finished — see compare_report.md ===")


if __name__ == "__main__":
    main()
