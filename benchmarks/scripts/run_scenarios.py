# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""Run benchmark scenarios defined in benchmarks/scenarios/*.yaml.

Each scenario runs the full pysewer pipeline (preprocessing -> routing ->
peak flow -> hydraulic design) on the declared dataset with the declared
config overrides, and records comparable metrics. Scenarios whose input
data is not staged locally are skipped.

Usage:
    python benchmarks/scripts/run_scenarios.py [scenario_name ...]
    python benchmarks/scripts/run_scenarios.py --out-dir work/benchmarks/results
"""

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import yaml

import pysewer
from pysewer.config.manager import reset_config, set_config
from pysewer.helper import get_edge_gdf

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios"


def load_scenarios(names):
    scenarios = []
    for path in sorted(SCENARIO_DIR.glob("*.yaml")):
        with open(path) as fh:
            scenario = yaml.safe_load(fh)
        scenario.setdefault("name", path.stem)
        if not names or scenario["name"] in names:
            scenarios.append(scenario)
    missing = set(names or []) - {s["name"] for s in scenarios}
    if missing:
        sys.exit(f"Unknown scenario(s): {', '.join(sorted(missing))}")
    return scenarios


def resolve(path_str):
    """Scenario paths are relative to the repository root."""
    path = Path(path_str)
    return path if path.is_absolute() else REPO_ROOT / path


def load_inputs(data):
    """Return (dem_path, roads_gdf_or_path, buildings_gdf_or_path, sink)."""
    if "gpkg" in data:
        gpkg = resolve(data["gpkg"])
        roads = gpd.read_file(gpkg, layer=data["roads_layer"])
        buildings = gpd.read_file(gpkg, layer=data["buildings_layer"])
        if "wwtp_layer" in data:
            wwtp = gpd.read_file(gpkg, layer=data["wwtp_layer"])
            sink = (wwtp.geometry.iloc[0].x, wwtp.geometry.iloc[0].y)
        else:
            sink = tuple(data["sink"])
        return resolve(data["dem"]), roads, buildings, sink
    return (
        resolve(data["dem"]),
        str(resolve(data["roads"])),
        str(resolve(data["buildings"])),
        tuple(data["sink"]),
    )


def data_available(data):
    paths = [data.get("dem")] + [
        data.get(k) for k in ("gpkg", "roads", "buildings") if k in data
    ]
    return all(resolve(p).exists() for p in paths if p)


def run_scenario(scenario):
    reset_config()
    if scenario.get("config"):
        set_config(custom_settings_dict=scenario["config"])

    dem, roads, buildings, sink = load_inputs(scenario["data"])
    md = pysewer.ModelDomain(str(dem), roads, buildings)
    md.add_sink([sink] if "gpkg" in scenario["data"] else sink)
    connection_graph = md.generate_connection_graph()
    layout = pysewer.rsph_tree(connection_graph, [sink])
    sewer = pysewer.estimate_peakflow(layout)
    G = pysewer.calculate_hydraulic_parameters(sewer, sinks=[sink])

    pipes = get_edge_gdf(G, detailed=True)
    gravity = pipes[~pipes["pressurized"]]
    violations = pipes["hydraulic_violations"].explode().dropna()
    # d_over_D is the true proportional flow depth (Butler's d/D). A max
    # well below max_depth_ratio (0.75) means every pipe runs far below
    # capacity, so the smallest available diameter governs — sizing is
    # floor-limited, not capacity-limited.
    max_fill = float(gravity["d_over_D"].max()) if len(gravity) else 0.0
    return {
        "scenario": scenario["name"],
        "description": scenario.get("description", ""),
        "config_overrides": scenario.get("config", {}),
        "n_pipes": len(pipes),
        "total_length_m": round(float(pipes.geometry.length.sum()), 1),
        "pressurized_length_m": round(
            float(pipes[pipes["pressurized"]].geometry.length.sum()), 1
        ),
        "n_pumping_stations": sum(
            1 for _, d in G.nodes(data=True) if d.get("pumping_station")
        ),
        "n_lifting_stations": sum(
            1 for _, d in G.nodes(data=True) if d.get("lifting_station")
        ),
        "diameter_distribution": {
            str(k): int(v)
            for k, v in sorted(pipes["diameter"].value_counts().items())
        },
        "diameter_distribution_gravity": {
            str(k): int(v)
            for k, v in sorted(gravity["diameter"].value_counts().items())
        },
        "hydraulic_violations": {
            str(k): int(v) for k, v in violations.value_counts().items()
        },
        "peak_flow_max_m3s": round(float(pipes["peak_flow"].max()), 6),
        "max_capacity_ratio_gravity": round(max_fill, 4),
    }


def write_summary(results, out_dir):
    lines = [
        "# Benchmark summary",
        "",
        "max fill = max proportional flow depth d/D across gravity pipes (design limit 0.75);",
        "well below the limit means diameters are floored by the available set, not by capacity.",
        "",
        "| scenario | pipes | length [m] | pressurized [m] | pumping | lifting | max fill | diameters |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        diameters = ", ".join(f"{d}: {n}" for d, n in r["diameter_distribution"].items())
        lines.append(
            f"| {r['scenario']} | {r['n_pipes']} | {r['total_length_m']} "
            f"| {r['pressurized_length_m']} | {r['n_pumping_stations']} "
            f"| {r['n_lifting_stations']} | {r['max_capacity_ratio_gravity']} "
            f"| {diameters} |"
        )
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*", help="scenario names (default: all)")
    parser.add_argument(
        "--out-dir",
        default=str(REPO_ROOT / "work" / "benchmarks" / "results"),
        help="output directory (default: work/benchmarks/results)",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for scenario in load_scenarios(args.scenarios):
        if not data_available(scenario["data"]):
            print(f"SKIP {scenario['name']}: input data not staged")
            continue
        print(f"RUN  {scenario['name']}")
        result = run_scenario(scenario)
        results.append(result)
        with open(out_dir / f"{scenario['name']}.json", "w") as fh:
            json.dump(result, fh, indent=2)

    if results:
        write_summary(results, out_dir)
        print(f"\nWrote {len(results)} result(s) + summary.md to {out_dir}")
    else:
        sys.exit("No scenario produced results (all skipped?)")


if __name__ == "__main__":
    main()
