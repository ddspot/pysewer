# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""Benchmark pysewer against the ELAN reference dataset.

Runs the full pysewer pipeline on the inputs provided by the ELAN project
(French colleagues; DEM_5m.tif + sewer_inputs.gpkg) and compares the result
with their reference outputs (sewer-outputs.gpkg), exercising the
ELAN-contributed features: population attribute on buildings, multi-layer
GPKG export with CRS, sink_coords, and total_static_head.

Usage (data staged under work/benchmarks/elan per the scratch convention):

    python tests/benchmarks/run_elan_benchmark.py [--data-dir work/benchmarks/elan]

Writes <data-dir>/pysewer_run/sewer-network.gpkg and
<data-dir>/pysewer_run/benchmark_report.md.
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

import pysewer
from pysewer.config.manager import set_config
from pysewer.export import write_gdf_to_gpkg
from pysewer.helper import get_edge_gdf, get_node_gdf


def run_pipeline(data_dir: Path, out_dir: Path):
    roads = gpd.read_file(data_dir / "sewer_inputs.gpkg", layer="roads_reduced_area")
    buildings = gpd.read_file(
        data_dir / "sewer_inputs.gpkg", layer="buildings_ign_population"
    )
    wwtp = gpd.read_file(data_dir / "sewer_inputs.gpkg", layer="wwtp")
    sink_coords = (wwtp.geometry.iloc[0].x, wwtp.geometry.iloc[0].y)
    crs = roads.crs

    # ELAN feature: population attribute drives the peak flow estimation
    set_config(
        custom_settings_dict={
            "optimization": {"inhabitants_dwelling_attribute_name": "population"}
        }
    )

    print(f"Inputs: {len(buildings)} buildings, {len(roads)} roads, sink {sink_coords}")
    model_domain = pysewer.ModelDomain(
        str(data_dir / "DEM_5m.tif"), roads, buildings
    )
    model_domain.add_sink([sink_coords])
    connection_graph = model_domain.generate_connection_graph()

    layout = pysewer.rsph_tree(connection_graph, [sink_coords])
    sewer_graph = pysewer.estimate_peakflow(layout)
    G = pysewer.calculate_hydraulic_parameters(sewer_graph, sinks=[sink_coords])

    # Multi-layer GPKG export with explicit CRS (ELAN feature)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_gpkg = out_dir / "sewer-network.gpkg"
    if out_gpkg.exists():
        out_gpkg.unlink()

    edges = get_edge_gdf(G, detailed=True)
    nodes = get_node_gdf(G, field="node_type", value="wwtp")
    all_nodes = get_node_gdf(G)

    node_data = dict(G.nodes(data=True))

    def node_subset(flag):
        sel = [n for n, d in node_data.items() if d.get(flag)]
        gdf = get_node_gdf(G.subgraph(sel)) if sel else gpd.GeoDataFrame(geometry=[])
        return gdf

    layers = {
        "sewer_pipes": edges,
        "sinks_layer": nodes,
        "lifting_stations": node_subset("lifting_station"),
        "pumping_stations": node_subset("pumping_station"),
    }
    for name, gdf in layers.items():
        if len(gdf):
            write_gdf_to_gpkg(gdf.copy(), str(out_gpkg), layer=name, crs=crs)
    print(f"Exported {out_gpkg}")
    return G, layers, crs


def summarize(tag, pipes, lifting, pumping, sinks):
    total_len = pipes["distance"].sum() if "distance" in pipes else pipes.length.sum()
    diam = (
        pipes["diameter"].value_counts().sort_index().to_dict()
        if "diameter" in pipes
        else {}
    )
    pressurized = (
        int(pipes["pressurized"].sum()) if "pressurized" in pipes else None
    )
    heads = pd.concat(
        [
            lifting["total_static_head"] if "total_static_head" in lifting else pd.Series(dtype=float),
            pumping["total_static_head"] if "total_static_head" in pumping else pd.Series(dtype=float),
        ]
    )
    return {
        "tag": tag,
        "n_pipes": len(pipes),
        "total_length_m": round(float(total_len), 1),
        "n_pressurized": pressurized,
        "diameters": diam,
        "n_lifting": len(lifting),
        "n_pumping": len(pumping),
        "n_sinks": len(sinks),
        "head_min": round(float(heads.min()), 2) if len(heads) else None,
        "head_max": round(float(heads.max()), 2) if len(heads) else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="work/benchmarks/elan", type=Path)
    args = ap.parse_args()
    data_dir = args.data_dir
    out_dir = data_dir / "pysewer_run"

    G, ours, crs = run_pipeline(data_dir, out_dir)

    ref = data_dir / "sewer-outputs.gpkg"
    ref_layers = {
        name: gpd.read_file(ref, layer=name)
        for name in ["sewer_pipes", "lifting_stations", "pumping_stations", "sinks_layer"]
    }

    ours_summary = summarize(
        "pysewer 0.2.0 (this run)",
        ours["sewer_pipes"],
        ours["lifting_stations"],
        ours["pumping_stations"],
        ours["sinks_layer"],
    )
    ref_summary = summarize(
        "ELAN reference",
        ref_layers["sewer_pipes"],
        ref_layers["lifting_stations"],
        ref_layers["pumping_stations"],
        ref_layers["sinks_layer"],
    )

    # CRS checks on our export
    crs_checks = {}
    for name in ours:
        if len(ours[name]):
            rt = gpd.read_file(out_dir / "sewer-network.gpkg", layer=name)
            crs_checks[name] = rt.crs.to_epsg() if rt.crs else None

    sink_node = ours["sinks_layer"]
    upstream_pe_ours = (
        float(sink_node["upstream_pe"].iloc[0]) if "upstream_pe" in sink_node else None
    )
    upstream_pe_ref = float(ref_layers["sinks_layer"]["upstream_pe"].iloc[0])

    lines = [
        "# ELAN benchmark report",
        "",
        f"Inputs: `{data_dir}` (EPSG:{crs.to_epsg()}), reference: `sewer-outputs.gpkg`",
        "",
        "| metric | this run | ELAN reference |",
        "|---|---|---|",
    ]
    for key in ["n_pipes", "total_length_m", "n_pressurized", "n_lifting", "n_pumping",
                "n_sinks", "head_min", "head_max"]:
        lines.append(f"| {key} | {ours_summary[key]} | {ref_summary[key]} |")
    lines += [
        f"| upstream_pe at sink | {upstream_pe_ours} | {upstream_pe_ref} |",
        f"| diameters (m: count) | {ours_summary['diameters']} | {ref_summary['diameters']} |",
        "",
        f"CRS of exported layers: {crs_checks} (expected {crs.to_epsg()} everywhere)",
        "",
        "Notes: the reference was produced by the ELAN fork prior to the",
        "min_cover fix (cover violations no longer force pumps), so pump and",
        "lifting station counts are expected to differ; topology, total pipe",
        "length and population-derived upstream PE should be close.",
    ]
    report = out_dir / "benchmark_report.md"
    report.write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\nReport written to {report}")


if __name__ == "__main__":
    sys.exit(main())
