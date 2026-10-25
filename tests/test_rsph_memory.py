# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""``rsph_tree`` without all-pairs Dijkstra returns exactly the 0.3.1 layout.

The 0.3.1 implementation (frozen in ``tests/_rsph_reference.py``) computed
all-pairs shortest paths and lengths, O(V^2) memory. The current one keeps one
distance row per terminal. These tests compare both, edge by edge with all
attributes, on the repository test data and on synthetic graphs built to
stress the tie-breaks: unit-weight grids (many equal paths), zero-cost edges,
parallel edges, direction-dependent weights, several sinks, unreachable
terminals and terminals lying on other terminals' paths.
"""

import contextlib
import copy
import io
import random
import warnings
from pathlib import Path

import networkx as nx
import pytest

import pysewer
from pysewer.routing import rsph_tree

from ._rsph_reference import rsph_tree_v031


def _layout(fn, G, sinks, **kwargs):
    H = copy.deepcopy(G)  # rsph_tree writes sink_coords into the edge data it copies
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        T = fn(H, list(sinks), **kwargs)
    edges = sorted((repr(u), repr(v), repr(sorted(d.items()))) for u, v, d in T.edges(data=True))
    nodes = sorted((repr(n), repr(sorted(d.items()))) for n, d in T.nodes(data=True))
    return edges, nodes, T.graph.get("unrouted_terminals")


def assert_same_layout(G, sinks, **kwargs):
    new = _layout(rsph_tree, G, sinks, **kwargs)
    old = _layout(rsph_tree_v031, G, sinks, **kwargs)
    assert new[2] == old[2], "unrouted terminals differ"
    assert new[1] == old[1], "node attributes differ"
    assert new[0] == old[0], "edges or edge attributes differ"
    return new


def _grid(n, seed, *, n_terminals, weights="unit", n_sinks=1, island=False, parallel=False):
    """A MultiDiGraph street grid with buildings on random grid nodes."""
    rng = random.Random(seed)
    G = nx.MultiDiGraph()
    for u, v in nx.grid_2d_graph(n, n).edges():
        if weights == "unit":
            w_uv = w_vu = 1.0
        elif weights == "zero":
            w_uv = w_vu = rng.choice([0.0, 0.0, 1.0, 2.0])
        elif weights == "asymmetric":  # e.g. pump penalty in one direction only
            w_uv = rng.uniform(1.0, 5.0)
            w_vu = w_uv * rng.choice([1.0, 1.0, 1000.0])
        else:
            w_uv = w_vu = rng.uniform(1.0, 5.0)
        G.add_edge(u, v, weight=w_uv, distance=abs(w_uv))
        G.add_edge(v, u, weight=w_vu, distance=abs(w_vu))
        if parallel and rng.random() < 0.3:  # a second, cheaper or dearer, parallel edge
            w = w_uv * rng.choice([0.5, 2.0])
            G.add_edge(u, v, weight=w, distance=w)
    nx.set_node_attributes(G, "road", "node_type")
    nodes = sorted(G.nodes)
    sinks = [nodes[0], nodes[-1]][:n_sinks]
    candidates = [x for x in nodes if x not in sinks]
    for t in rng.sample(candidates, n_terminals):
        G.nodes[t]["node_type"] = "building"
    if island:  # buildings on a separate road piece without a sink
        for k in range(3):
            a, b = ("island", k), ("island_b", k)
            G.add_node(a, node_type="building")
            G.add_node(b, node_type="road")
            G.add_edge(a, b, weight=1.0, distance=1.0)
            G.add_edge(b, a, weight=1.0, distance=1.0)
    return G, sinks


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("weights", ["unit", "zero", "asymmetric", "random"])
def test_grid_layouts_match_031(weights, seed):
    G, sinks = _grid(9, seed, n_terminals=25, weights=weights, parallel=(weights == "random"))
    assert_same_layout(G, sinks)


@pytest.mark.parametrize("seed", range(4))
def test_two_sinks_and_islands_match_031(seed):
    G, sinks = _grid(8, seed, n_terminals=20, weights="unit", n_sinks=2, island=True)
    _, _, unrouted = assert_same_layout(G, sinks)
    assert len(unrouted) == 3  # the island buildings


def test_skip_nodes_match_031():
    G, sinks = _grid(8, 11, n_terminals=20)
    skip = [n for n, d in G.nodes(data=True) if d["node_type"] == "building"][:5]
    assert_same_layout(G, sinks, skip_nodes=skip)


def test_dense_terminals_on_each_others_paths_match_031():
    # most grid nodes are buildings, so many paths pass through other buildings
    G, sinks = _grid(7, 3, n_terminals=40, weights="unit")
    assert_same_layout(G, sinks)


def test_repository_test_data_matches_031():
    sink = (691350, 2553250)
    data = Path("tests") / "test_data"
    domain = pysewer.ModelDomain(data / "dem.tif", data / "roads_clipped.shp", data / "buildings_clipped.shp")
    domain.add_sink(sink)
    G = domain.generate_connection_graph()
    edges, _, unrouted = assert_same_layout(G, [sink])
    assert edges and unrouted == []
