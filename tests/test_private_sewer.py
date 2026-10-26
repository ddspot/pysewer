# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""``add_private_sewer: False`` designs the public network without house connections."""

import shutil
from pathlib import Path

import networkx as nx
import pytest

import pysewer
from pysewer.helper import get_node_keys, get_sewer_info
from pysewer.optimization import (
    calculate_hydraulic_parameters,
    estimate_peakflow,
    reverse_bfs,
)
from pysewer.routing import rsph_tree

TEST_DATA = Path("tests") / "test_data"
SINK = (691350, 2553250)


def _network(tmp_path, add_private_sewer):
    shutil.copy(TEST_DATA / "dem.tif", tmp_path / "dem.tif")
    pysewer.set_custom_config(
        custom_settings_dict={"preprocessing": {"add_private_sewer": add_private_sewer}}
    )
    try:
        md = pysewer.ModelDomain(
            tmp_path / "dem.tif",
            TEST_DATA / "roads_clipped.shp",
            TEST_DATA / "buildings_clipped.shp",
        )
        md.add_sink(SINK)
        tree = rsph_tree(md.generate_connection_graph(), [SINK])
        estimate_peakflow(tree, inhabitants_dwelling=3, daily_wastewater_person=0.15)
        calculate_hydraulic_parameters(tree, sinks=[SINK])
    finally:
        pysewer.set_custom_config()
    return tree


@pytest.fixture(scope="module")
def networks(tmp_path_factory):
    return {
        flag: _network(tmp_path_factory.mktemp(str(flag)), flag) for flag in (True, False)
    }


def test_every_building_is_routed(networks):
    # 0.3.1: no building was linked to a road, so nothing was routed
    tree = networks[False]
    assert tree.graph["unrouted_terminals"] == []
    info = get_sewer_info(tree)
    assert info["Connected Buildings"] == info["Total Buildings"] == 57


def test_same_layout_as_with_private_sewers(networks):
    assert set(networks[False].edges) == set(networks[True].edges)


def test_house_connections_are_left_out_of_the_design(networks):
    with_ps, without = (get_sewer_info(networks[f]) for f in (True, False))
    buildings = set(get_node_keys(networks[False], field="node_type", value="building"))
    house = [(u, v) for u, v in networks[False].edges if u in buildings or v in buildings]
    assert house
    assert all(networks[False].edges[e]["pressurized"] is None for e in house)
    assert without["Gravity Sewer Length [m]"] < with_ps["Gravity Sewer Length [m]"]
    assert without["Private Pumps"] == 0
    for k in ("Lifting Stations", "Pumping Stations"):
        assert without[k] == with_ps[k]


def test_reverse_bfs_leaves_graph_unchanged():
    # 0.3.1 removed the buildings from the caller's graph
    G = nx.DiGraph()
    G.add_node("b", node_type="building")
    G.add_edges_from([("b", "r"), ("r", "s")])
    edges = list(reverse_bfs(G, "s", include_private_sewer=False))
    assert edges == [("r", "s")]
    assert set(G.nodes) == {"b", "r", "s"}
