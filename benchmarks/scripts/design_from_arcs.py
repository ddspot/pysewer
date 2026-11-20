# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""Run the pysewer design step on a given set of arcs.

The script rebuilds the connection graph of a case, takes the arcs of a
solution (instance ids, as written by ``export_steiner_instances.py``) and
runs the pysewer design step on them: ``estimate_peakflow``, then
``calculate_hydraulic_parameters``. It reports the routing cost, the stations
and the sewer length. The pysewer RSPH tree goes through the same steps, as
the reference.

Usage::

    python benchmarks/scripts/design_from_arcs.py CASE.yaml \
        --instance INSTANCE.json --arcs SOLUTION.json [--arcs ...] \
        [--out RESULT.json]

A solution file holds ``{"arcs": [[u, v], ...]}`` or the list ``[[u, v], ...]``.
The arcs point downstream, from the buildings towards the root, as in the
instance. The ids are the instance ids. Give the instance file with
``--instance``: the script then checks that the rebuilt graph has the same
nodes and arcs as the instance. Use the pysewer version and the settings of
the instance.
"""

import argparse
import json
import math
from pathlib import Path

import networkx as nx
from export_steiner_instances import build_case, node_ids

import pysewer
from pysewer.helper import get_sewer_info


def tree_from_arcs(G, sinks, arcs) -> nx.DiGraph:
    """The sewer graph that ``rsph_tree`` would return for these arcs.

    ``arcs`` are ``(u, v)`` pairs of pysewer nodes. Of parallel edges, the
    cheapest one is used, as in the instance.
    """
    tree = nx.DiGraph(nx.create_empty_copy(G))
    sink = str(sinks[0])
    for u, v in arcs:
        if not G.has_edge(u, v):
            raise ValueError(f"arc {u} -> {v} is not in the connection graph")
        attrs = dict(min(G[u][v].values(), key=lambda d: d["weight"]))
        attrs["sink_coords"] = sink
        tree.add_edge(u, v, **attrs)
        tree.nodes[u]["sink_coords"] = sink
        tree.nodes[v]["sink_coords"] = sink
    tree.graph["unrouted_terminals"] = []
    check_tree(G, sinks[0], tree)
    return tree


def check_tree(G, root, tree) -> None:
    """The arcs form an in-tree to the root that reaches every building."""
    T = tree.edge_subgraph(tree.edges)
    buildings = {n for n, d in G.nodes(data=True) if d.get("node_type") == "building"}
    if not nx.is_arborescence(T.reverse(copy=False)):
        raise ValueError("the arcs do not form an in-tree")
    if root not in T or T.out_degree(root) != 0:
        raise ValueError("the arcs do not end at the root")
    missing = buildings - set(T)
    if missing:
        raise ValueError(f"the arcs do not reach {len(missing)} buildings")


def design(G, sinks, tree, label: str) -> dict:
    designed = pysewer.calculate_hydraulic_parameters(
        pysewer.estimate_peakflow(tree), sinks=sinks
    )
    info = get_sewer_info(designed)
    cost = sum(min(d["weight"] for d in G[u][v].values()) for u, v in tree.edges)
    return {
        "solution": label,
        "arcs": tree.number_of_edges(),
        "cost": cost,
        "pumping_stations": info["Pumping Stations"],
        "lifting_stations": info["Lifting Stations"],
        "gravity_length": info["Gravity Sewer Length [m]"],
        "pressurized_length": info["Pressurized Sewer Length [m]"],
    }


def check_instance(G, ids, instance: dict) -> None:
    """The rebuilt graph has the nodes and arcs of the instance."""
    by_id = {n: i for i, n in ids.items()}
    if len(by_id) != len(instance["nodes"]):
        raise ValueError(
            f"{len(by_id)} nodes in the graph, {len(instance['nodes'])} in the instance"
        )
    for node in instance["nodes"]:
        x, y = by_id[node["id"]]
        if not (math.isclose(x, node["x"]) and math.isclose(y, node["y"])):
            raise ValueError(f"node {node['id']} is at another position")
    for arc in instance["arcs"]:
        u, v = by_id[arc["u"]], by_id[arc["v"]]
        weight = min(d["weight"] for d in G[u][v].values())
        if not math.isclose(weight, arc["weight"]):
            raise ValueError(
                f"arc {arc['u']} -> {arc['v']}: weight {weight} in the graph, "
                f"{arc['weight']} in the instance"
            )


def read_arcs(path: Path) -> list:
    data = json.loads(path.read_text())
    return data["arcs"] if isinstance(data, dict) else data


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case", type=Path)
    parser.add_argument("--instance", type=Path)
    parser.add_argument("--arcs", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    case, G, sinks = build_case(args.case.resolve())
    ids = node_ids(G)
    by_id = {i: n for n, i in ids.items()}
    if args.instance:
        check_instance(G, ids, json.loads(args.instance.read_text()))

    rows = [design(G, sinks, pysewer.rsph_tree(G, sinks), "rsph")]
    for path in args.arcs:
        arcs = [(by_id[u], by_id[v]) for u, v in read_arcs(path)]
        rows.append(design(G, sinks, tree_from_arcs(G, sinks, arcs), path.stem))

    for row in rows:
        print(
            f"{case['name']} {row['solution']}: cost {row['cost']:.1f}, "
            f"{row['pumping_stations']} pumping stations, "
            f"{row['lifting_stations']} lifting stations, "
            f"gravity {row['gravity_length']:.0f} m, "
            f"pressurized {row['pressurized_length']:.0f} m"
        )
    if args.out:
        args.out.write_text(
            json.dumps({"case": case["name"], "results": rows}, indent=1)
        )


if __name__ == "__main__":
    main()
