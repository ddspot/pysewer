# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""Run one pysewer pipeline in an isolated process and write metrics JSON.

Used by compare_versions.py to benchmark the published pysewer (v0.1.20)
against the current checkout with identical inputs and design arguments.

    python pipeline_worker.py <spec.json>

Spec fields: mode ("current"|"v0120"), baseline_path (v0120 only: worktree
to prepend to sys.path), dem, roads, buildings (path or {path, layer}),
sink [x, y], design {...explicit kwargs...}, out (result JSON path).
"""

import json
import sys
import time
import types
from pathlib import Path


def _load_vector(spec_entry):
    import geopandas as gpd

    if isinstance(spec_entry, dict):
        return gpd.read_file(spec_entry["path"], layer=spec_entry.get("layer"))
    return gpd.read_file(spec_entry)


def main(spec_path):
    spec = json.loads(Path(spec_path).read_text())
    result = {"mode": spec["mode"], "error": None}
    t0 = time.time()
    try:
        if spec["mode"] == "v0120":
            # the published plotting module imports earthpy (plot-only dep,
            # dropped from our env); stub it before importing pysewer
            for name in ("earthpy", "earthpy.plot", "earthpy.spatial"):
                sys.modules[name] = types.ModuleType(name)
            sys.path.insert(0, spec["baseline_path"])

        import pysewer  # resolves to baseline or installed depending on path

        result["pysewer_file"] = pysewer.__file__

        design = spec["design"]
        roads = _load_vector(spec["roads"])
        buildings = _load_vector(spec["buildings"])
        sink = tuple(spec["sink"])

        # clustering="none" on both sides: the published cluster_centers
        # breaks under pandas 3, and identical settings keep the comparison
        # apples-to-apples
        md = pysewer.ModelDomain(
            spec["dem"], roads, buildings, clustering="none"
        )
        md.add_sink(sink)
        cg = md.generate_connection_graph()
        layout = pysewer.rsph_tree(cg, [sink])
        sewer = pysewer.estimate_peakflow(
            layout,
            inhabitants_dwelling=design["inhabitants_dwelling"],
            daily_wastewater_person=design["daily_wastewater_person"],
            peak_factor=design["peak_factor"],
        )
        G = pysewer.calculate_hydraulic_parameters(
            sewer,
            sinks=[sink],
            pressurized_diameter=design["pressurized_diameter"],
            diameters=design["diameters"],
            roughness=design["roughness"],
        )

        from pysewer.helper import get_edge_gdf

        pipes = get_edge_gdf(G, detailed=True)
        pressurized = (
            pipes[pipes["pressurized"]]
            if "pressurized" in pipes.columns
            else pipes.iloc[0:0]
        )
        result.update(
            {
                "n_pipes": len(pipes),
                "total_length_m": round(float(pipes.geometry.length.sum()), 1),
                "pressurized_length_m": round(
                    float(pressurized.geometry.length.sum()), 1
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
                "peak_flow_max_m3s": round(float(pipes["peak_flow"].max()), 6),
            }
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["runtime_s"] = round(time.time() - t0, 1)
    Path(spec["out"]).write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in ("mode", "error", "runtime_s")}))


if __name__ == "__main__":
    main(sys.argv[1])
