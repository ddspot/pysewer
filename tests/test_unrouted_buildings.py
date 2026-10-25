# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""Buildings that cannot reach a sink are reported, never dropped silently."""

import shutil
from pathlib import Path

import networkx as nx
import pytest
from shapely.geometry import LineString, Point

import pysewer
from pysewer.helper import get_sewer_info
from pysewer.routing import UnroutedTerminalsError, rsph_tree, rsph_tree_fast

TEST_DATA = Path("tests") / "test_data"


def _graph_with_island():
    """b1 reaches the sink; b2 sits on a road island without a sink."""
    G = nx.MultiDiGraph()
    G.add_node("sink", node_type="wwtp")
    for n in ("b1", "b2"):
        G.add_node(n, node_type="building")
    for u, v in [("b1", "r1"), ("r1", "sink"), ("b2", "r2")]:
        G.add_edge(u, v, weight=1.0, distance=1.0)
        G.add_edge(v, u, weight=1.0, distance=1.0)
    return G


def test_rsph_tree_reports_unrouted_building():
    with pytest.warns(UserWarning, match="no path to a sink"):
        tree = rsph_tree(_graph_with_island(), ["sink"])
    assert tree.graph["unrouted_terminals"] == ["b2"]
    assert nx.has_path(tree, "b1", "sink")
    assert "b2" in tree and tree.degree("b2") == 0

    info = get_sewer_info(tree)
    assert info["Total Buildings"] == 2
    assert info["Connected Buildings"] == 1
    assert info["Unconnected Buildings"] == 1


def test_rsph_tree_strict_raises():
    with pytest.raises(UnroutedTerminalsError) as err:
        rsph_tree(_graph_with_island(), ["sink"], strict=True)
    assert err.value.unrouted == ["b2"]


def test_rsph_tree_fast_reports_unrouted_building():
    with pytest.warns(UserWarning, match="no path to a sink"):
        tree = rsph_tree_fast(_graph_with_island(), "sink")
    assert tree.graph["unrouted_terminals"] == ["b2"]
    assert nx.has_path(tree, "b1", "sink")


def test_rsph_tree_fast_accepts_a_list_of_sinks():
    tree = rsph_tree_fast(_graph_with_island().subgraph(["b1", "r1", "sink"]), ["sink"])
    assert tree.graph["unrouted_terminals"] == []
    assert nx.has_path(tree, "b1", "sink")
    assert tree.edges["b1", "r1"]["distance"] == 1.0


def test_rsph_tree_all_connected_has_empty_list():
    G = _graph_with_island()
    G.remove_node("b2")
    tree = rsph_tree(G, ["sink"])
    assert tree.graph["unrouted_terminals"] == []
    assert get_sewer_info(tree)["Unconnected Buildings"] == 0


def test_rsph_tree_sinks_in_separate_components():
    """Each building routes to the sink of its own component (previously
    every terminal was skipped and nothing was routed)."""
    G = nx.MultiDiGraph()
    for s in ("s1", "s2"):
        G.add_node(s, node_type="wwtp")
    for b in ("b1", "b2"):
        G.add_node(b, node_type="building")
    for u, v in [("b1", "s1"), ("b2", "s2")]:
        G.add_edge(u, v, weight=1.0, distance=1.0)
        G.add_edge(v, u, weight=1.0, distance=1.0)
    tree = rsph_tree(G, ["s1", "s2"])
    assert tree.graph["unrouted_terminals"] == []
    assert nx.has_path(tree, "b1", "s1")
    assert nx.has_path(tree, "b2", "s2")


@pytest.fixture
def model_domain(tmp_path):
    dem = tmp_path / "dem.tif"  # pysewer rewrites the DEM it loads
    shutil.copy(TEST_DATA / "dem.tif", dem)
    return pysewer.ModelDomain(
        dem, TEST_DATA / "roads_clipped.shp", TEST_DATA / "buildings_clipped.shp"
    )


def test_add_node_with_stale_closest_edge(model_domain):
    """Two cluster centers whose pre-computed closest edge is the same: the
    first insertion splits that edge, the second must look it up again
    instead of failing and ending up with a self-loop and no road link."""
    G = model_domain.connection_graph
    loops_before = nx.number_of_selfloops(G)  # zero-length road loops
    u, v = max(G.edges(), key=lambda e: Point(e[0]).distance(Point(e[1])))
    edge = LineString([u, v])
    p1 = edge.interpolate(0.3, normalized=True)
    p2 = edge.interpolate(0.7, normalized=True)
    c1, c2 = Point(p1.x + 15, p1.y), Point(p2.x + 15, p2.y)

    model_domain.add_node(c1, "cluster_center", edge)
    assert not G.has_edge(u, v)  # the pre-computed edge is now stale
    model_domain.add_node(c2, "cluster_center", edge)

    assert nx.number_of_selfloops(G) == loops_before
    assert not G.has_edge((c2.x, c2.y), (c2.x, c2.y))
    assert nx.is_connected(G)


def test_cluster_centers_connect_every_building(model_domain):
    pysewer.set_custom_config(
        custom_settings_dict={"preprocessing": {"clustering": "centers"}}
    )
    try:
        sink = (691350, 2553250)
        model_domain.add_sink(sink)
        G = model_domain.generate_connection_graph()
        tree = rsph_tree(G, [sink])
    finally:
        pysewer.set_custom_config()
    assert tree.graph["unrouted_terminals"] == []
    info = get_sewer_info(tree)
    assert info["Connected Buildings"] == info["Total Buildings"] == 57
