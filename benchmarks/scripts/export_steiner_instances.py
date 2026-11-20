# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""Export pysewer routing problems as solver-independent Steiner instances.

For each case, the connection graph pysewer routes on is written as one JSON
file, together with the terminals (buildings), the root (outlet) and the tree
pysewer's repeated shortest path heuristic (RSPH) picks on it. An external
Steiner solver can then work on exactly the problem pysewer solves, and its
result can be compared with RSPH on the same cost, without installing pysewer.

Usage::

    python benchmarks/scripts/export_steiner_instances.py CASE.yaml [...] \
        --out work/benchmarks/steiner_instances

A case file holds ``name``, ``dem``, ``roads``, ``buildings`` and ``sink``
(``[x, y]`` or ``lowest``), and optionally ``description`` and ``crs``.
Paths are relative to the case file, or else to the repository root.
"""

import argparse
import json
import math
import time
import warnings
from datetime import date
from importlib.metadata import version
from pathlib import Path

import geopandas as gpd
import networkx as nx
import yaml

import pysewer
from pysewer.config.manager import get_config

REPO = Path(__file__).resolve().parents[2]
FORMAT = "pysewer-steiner-instance/1"


def resolve(case_path: Path, value: str) -> Path:
    for base in (case_path.parent, REPO):
        if (base / value).exists():
            return base / value
    raise FileNotFoundError(f"{value} (from {case_path})")


def read_layer(path: Path, crs: str | None) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    # Some inputs carry a compound CRS (UTM + unknown vertical datum).
    return gdf.set_crs(crs, allow_override=True) if crs else gdf


def build_case(case_path: Path):
    """``(case, connection_graph, sinks)`` for a case file. The case has
    exactly one sink."""
    case = yaml.safe_load(case_path.read_text())
    crs = case.get("crs")
    roads = read_layer(resolve(case_path, case["roads"]), crs)
    buildings = read_layer(resolve(case_path, case["buildings"]), crs)
    dem = resolve(case_path, case["dem"])

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Geometry column does not contain geometry"
        )
        md = pysewer.ModelDomain(str(dem), roads, buildings)
    if case["sink"] == "lowest":
        md.set_sink_lowest()
    else:
        md.add_sink(tuple(case["sink"]))
    sinks = md.get_sinks()
    if len(sinks) != 1:
        raise ValueError(f"{case['name']}: expected one sink, got {len(sinks)}")
    return case, md.generate_connection_graph(), sinks


def node_ids(G) -> dict:
    """Instance id of each node. Nodes are coordinate tuples in pysewer; the
    ids number them in sorted order."""
    return {n: i for i, n in enumerate(sorted(G.nodes))}


def export_case(case_path: Path, out_dir: Path) -> dict:
    t0 = time.perf_counter()
    case, G, sinks = build_case(case_path)
    crs = case.get("crs")
    tree = pysewer.rsph_tree(G, sinks)
    seconds = time.perf_counter() - t0

    ids = node_ids(G)
    nodes = [
        {
            "id": ids[n],
            "x": n[0],
            "y": n[1],
            "elevation": d.get("elevation"),
            "type": d.get("node_type") or "road",
        }
        for n, d in sorted(G.nodes(data=True))
    ]
    # The connection graph is a MultiDiGraph; keep the cheapest of any
    # parallel arcs, which is the one shortest-path routing would use.
    arcs = {}
    for u, v, d in G.edges(data=True):
        key = (ids[u], ids[v])
        if key not in arcs or d["weight"] < arcs[key]["weight"]:
            arcs[key] = {
                "weight": d["weight"],
                "distance": d["distance"],
                "needs_pump": bool(d.get("needs_pump")),
            }
    terminals = sorted(
        ids[n] for n, d in G.nodes(data=True) if d.get("node_type") == "building"
    )
    unrouted = sorted(ids[n] for n in tree.graph.get("unrouted_terminals", []))
    rsph_arcs = sorted((ids[u], ids[v]) for u, v in tree.edges)
    rsph_cost = sum(arcs[a]["weight"] for a in rsph_arcs)

    config = get_config()
    instance = {
        "format": FORMAT,
        "name": case["name"],
        "description": " ".join(str(case.get("description", "")).split()),
        "crs": str(G.graph.get("crs") or crs or ""),
        "created": date.today().isoformat(),
        "pysewer_version": version("pysewer"),
        "settings": {
            "dx": config.preprocessing.dx,
            "pump_penalty": config.preprocessing.pump_penalty,
            "max_connection_length": config.preprocessing.max_connection_length,
            "min_slope": config.optimization.min_slope,
            "tmin": config.optimization.tmin,
            "tmax": config.optimization.tmax,
        },
        "direction": "arcs point downstream, from the buildings towards the root",
        "root": ids[sinks[0]],
        "terminals": terminals,
        "unrouted_terminals": unrouted,
        "nodes": nodes,
        "arcs": [{"u": u, "v": v, **a} for (u, v), a in sorted(arcs.items())],
        "rsph": {"arcs": [list(a) for a in rsph_arcs], "cost": rsph_cost},
    }
    check(instance)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{case['name']}.json").write_text(
        json.dumps(instance, separators=(",", ":"))
    )

    return {
        "name": case["name"],
        "nodes": len(nodes),
        "arcs": len(arcs),
        "terminals": len(terminals),
        "unrouted": len(unrouted),
        "needs_pump": sum(a["needs_pump"] for a in arcs.values()),
        "rsph_arcs": len(rsph_arcs),
        "rsph_cost": rsph_cost,
        "seconds": seconds,
    }


def to_digraph(instance: dict) -> nx.DiGraph:
    """The instance as a networkx DiGraph, arcs pointing downstream."""
    D = nx.DiGraph()
    D.add_nodes_from((n["id"], n) for n in instance["nodes"])
    D.add_edges_from((a["u"], a["v"], a) for a in instance["arcs"])
    return D


def check(instance: dict) -> None:
    """The RSPH tree is an in-tree to the root over the instance's arcs,
    reaches every routed terminal, and its cost matches."""
    D = to_digraph(instance)
    T = D.edge_subgraph(map(tuple, instance["rsph"]["arcs"]))
    root = instance["root"]
    routed = set(instance["terminals"]) - set(instance["unrouted_terminals"])
    assert nx.is_arborescence(T.reverse(copy=False)), "RSPH tree is not an in-tree"
    assert root in T and T.out_degree(root) == 0, "RSPH tree does not end at the root"
    assert routed <= set(T), "RSPH tree misses routed terminals"
    cost = sum(D[u][v]["weight"] for u, v in T.edges)
    assert math.isclose(cost, instance["rsph"]["cost"]), "RSPH cost mismatch"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cases", nargs="+", type=Path)
    parser.add_argument(
        "--out", type=Path, default=REPO / "work/benchmarks/steiner_instances"
    )
    args = parser.parse_args()

    rows = []
    for case_path in args.cases:
        row = export_case(case_path.resolve(), args.out)
        print(
            f"{row['name']}: {row['nodes']} nodes, {row['arcs']} arcs, "
            f"{row['terminals']} terminals ({row['unrouted']} unrouted), "
            f"RSPH cost {row['rsph_cost']:.1f} ({row['seconds']:.0f} s)",
            flush=True,
        )
        rows.append(row)
    (args.out / "summary.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
