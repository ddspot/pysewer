# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""Compare the published pysewer (default v0.1.20) against the current
checkout on a benchmark case: identical inputs, identical explicit design
arguments, separate worker processes.

    python benchmarks/scripts/compare_versions.py <case.yaml> [...]
    python benchmarks/scripts/compare_versions.py --all

Case YAML (benchmarks/cases/*.yaml — paths relative to the repo root):

    name: example_case
    dem: work/benchmarks/example/dem.tif
    roads: work/benchmarks/example/roads.shp        # or {path, layer}
    buildings: work/benchmarks/example/buildings.shp
    sink: [690500, 2557000]      # or "lowest" (lowest road vertex on DEM)
    design:                       # explicit args passed to BOTH versions
      diameters: [0.2, 0.3, 0.4, 0.5, 1.0]
      pressurized_diameter: 0.2
      roughness: 0.013
      inhabitants_dwelling: 3
      daily_wastewater_person: 0.15
      peak_factor: 4.0

Results: work/benchmarks/results/compare_<name>.json and a rolling
work/benchmarks/results/compare_report.md.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CASE_DIR = Path(__file__).resolve().parent.parent / "cases"
WORKER = Path(__file__).resolve().parent / "pipeline_worker.py"
BASELINE_REF = "v0.1.20"
BASELINE_DIR = REPO_ROOT / "work" / "benchmarks" / f"baseline_{BASELINE_REF}"
DEFAULT_TIMEOUT_S = 4 * 3600

DEFAULT_DESIGN = {
    "diameters": [0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0],
    "pressurized_diameter": 0.2,
    "roughness": 0.013,
    "inhabitants_dwelling": 3,
    "daily_wastewater_person": 0.15,
    "peak_factor": 4.0,
}


def ensure_baseline():
    if not (BASELINE_DIR / "pysewer" / "__init__.py").exists():
        BASELINE_DIR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "--force", str(BASELINE_DIR), BASELINE_REF],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
    return BASELINE_DIR


def resolve(path_str):
    p = Path(path_str)
    return str(p if p.is_absolute() else REPO_ROOT / p)


def resolve_vector(entry):
    if isinstance(entry, dict):
        return {"path": resolve(entry["path"]), "layer": entry.get("layer")}
    return resolve(entry)


def lowest_road_vertex(dem_path, roads_entry):
    """Deterministic sink: the road vertex with the lowest DEM elevation."""
    import geopandas as gpd
    import numpy as np
    import rasterio

    if isinstance(roads_entry, dict):
        roads = gpd.read_file(roads_entry["path"], layer=roads_entry.get("layer"))
    else:
        roads = gpd.read_file(roads_entry)
    coords = []
    for geom in roads.geometry:
        if geom is None:
            continue
        geoms = geom.geoms if hasattr(geom, "geoms") else [geom]
        for g in geoms:
            coords.extend(list(g.coords))
    coords = [(x, y) for x, y, *rest in [c if len(c) > 2 else (*c, 0) for c in coords]]
    with rasterio.open(dem_path) as src:
        elevations = np.array([v[0] for v in src.sample(coords)])
        nodata = src.nodata
    valid = np.isfinite(elevations)
    if nodata is not None:
        valid &= elevations != nodata
    idx = int(np.argmin(np.where(valid, elevations, np.inf)))
    return list(coords[idx])


def run_worker(mode, case, sink, out_path, timeout_s):
    spec = {
        "mode": mode,
        "baseline_path": str(BASELINE_DIR),
        "dem": resolve(case["dem"]),
        "roads": resolve_vector(case["roads"]),
        "buildings": resolve_vector(case["buildings"]),
        "sink": sink,
        "design": {**DEFAULT_DESIGN, **case.get("design", {})},
        "out": str(out_path),
    }
    spec_path = out_path.with_suffix(".spec.json")
    spec_path.write_text(json.dumps(spec, indent=2))
    # remove any stale result: if the worker dies without writing (e.g. OOM
    # kill), reading a leftover file would silently misreport the old run
    out_path.unlink(missing_ok=True)
    try:
        proc = subprocess.run(
            [sys.executable, str(WORKER), str(spec_path)],
            check=False,
            timeout=timeout_s,
            capture_output=True,
        )
        if not out_path.exists():
            out_path.write_text(
                json.dumps(
                    {
                        "mode": mode,
                        "error": f"worker died without result (exit {proc.returncode})",
                    }
                )
            )
    except subprocess.TimeoutExpired:
        out_path.write_text(
            json.dumps({"mode": mode, "error": f"timeout after {timeout_s}s"})
        )
    return json.loads(out_path.read_text())


def compare_case(case_path, out_dir, timeout_s=DEFAULT_TIMEOUT_S):
    with open(case_path) as fh:
        case = yaml.safe_load(fh)
    name = case.get("name", Path(case_path).stem)
    print(f"=== {name}")
    ensure_baseline()

    sink = case["sink"]
    if sink == "lowest":
        sink = lowest_road_vertex(resolve(case["dem"]), resolve_vector(case["roads"]))
        print(f"    sink (lowest road vertex): {sink}")

    results = {}
    for mode in ("v0120", "current"):
        out_path = out_dir / f"compare_{name}.{mode}.json"
        results[mode] = run_worker(mode, case, sink, out_path, timeout_s)
        status = results[mode].get("error") or f"ok ({results[mode]['runtime_s']}s)"
        print(f"    {mode}: {status}")

    comparison = {"case": name, "sink": sink, **{m: results[m] for m in results}}
    (out_dir / f"compare_{name}.json").write_text(json.dumps(comparison, indent=2))
    return comparison


def append_report(comparison, out_dir):
    report = out_dir / "compare_report.md"
    lines = [] if report.exists() else [
        "# Published (v0.1.20) vs current main — comparison report",
        "",
        "| case | version | pipes | length [m] | pumps | lifts | diameters | runtime [s] | error |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for mode in ("v0120", "current"):
        r = comparison[mode]
        if r.get("error"):
            lines.append(
                f"| {comparison['case']} | {mode} | — | — | — | — | — | {r.get('runtime_s', '—')} | {r['error'][:80]} |"
            )
        else:
            diameters = ", ".join(
                f"{d}: {n}" for d, n in r["diameter_distribution"].items()
            )
            lines.append(
                f"| {comparison['case']} | {mode} | {r['n_pipes']} | {r['total_length_m']} "
                f"| {r['n_pumping_stations']} | {r['n_lifting_stations']} | {diameters} "
                f"| {r['runtime_s']} | |"
            )
    with open(report, "a") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="*", help="case YAML paths or names")
    parser.add_argument("--all", action="store_true", help="run all benchmarks/cases/*.yaml")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    args = parser.parse_args()

    if args.all:
        case_paths = sorted(CASE_DIR.glob("*.yaml"))
    else:
        case_paths = [
            Path(c) if Path(c).exists() else CASE_DIR / f"{c}.yaml" for c in args.cases
        ]
    if not case_paths:
        sys.exit("no cases given (use --all or name cases)")

    out_dir = REPO_ROOT / "work" / "benchmarks" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    for case_path in case_paths:
        comparison = compare_case(case_path, out_dir, args.timeout)
        append_report(comparison, out_dir)
    print(f"report: {out_dir / 'compare_report.md'}")


if __name__ == "__main__":
    main()
