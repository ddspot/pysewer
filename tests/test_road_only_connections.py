# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""Buildings connect to the street, not through a neighbour's house connection,
and the network does not depend on the row order of the input files."""

from pathlib import Path

import geopandas as gpd
import networkx as nx
import pytest
from shapely.geometry import LineString, Point

import pysewer
from pysewer.helper import get_node_keys
from pysewer.routing import rsph_tree

TEST_DATA = Path("tests") / "test_data"
SINK = (691350, 2553250)
ROADS = TEST_DATA / "roads_clipped.shp"
BUILDINGS = TEST_DATA / "buildings_clipped.shp"


def _domain(roads=ROADS, buildings=BUILDINGS, **kwargs):
    return pysewer.ModelDomain(TEST_DATA / "dem.tif", roads, buildings, **kwargs)


@pytest.fixture(scope="module")
def domain():
    return _domain()


def test_buildings_attach_to_road_edges(domain):
    G = domain.connection_graph
    for b in get_node_keys(G, field="node_type", value="building"):
        for n in G[b]:
            if G.nodes[n].get("node_type") == "cluster_center":
                continue
            # the connection point lies on a street: it has road edges besides
            # the house connections that end there
            road = [m for m in G[n] if not G.edges[n, m].get("private_sewer")]
            assert len(road) >= 2, f"building {b} attaches to a house connection"


def test_every_building_edge_is_a_house_connection(domain):
    G = domain.connection_graph
    for b in get_node_keys(G, field="node_type", value="building"):
        for n in G[b]:
            assert G.edges[b, n].get("private_sewer") is True


def test_cluster_center_links_are_house_connections():
    md = _domain(connect_buildings=False)
    md.connect_buildings(max_connection_length=20)
    G = md.connection_graph
    centers = set(get_node_keys(G, field="node_type", value="cluster_center"))
    assert centers, "test data should produce cluster centers at 20 m"
    links = [(u, v) for u, v in G.edges if u in centers or v in centers]
    to_buildings = [
        (u, v)
        for u, v in links
        if "building" in (G.nodes[u].get("node_type"), G.nodes[v].get("node_type"))
    ]
    assert to_buildings
    assert all(G.edges[e].get("private_sewer") is True for e in to_buildings)


def test_split_house_connection_keeps_flag(domain):
    G = nx.Graph()
    G.add_edge((0.0, 0.0), (10.0, 0.0), private_sewer=True)
    G.add_edge((10.0, 0.0), (20.0, 0.0))
    domain.connect_to_roadnetwork(
        G, Point(5, 3), Point(5, 0), LineString([(0, 0), (10, 0)])
    )
    assert G.edges[(0.0, 0.0), (5.0, 0.0)]["private_sewer"] is True
    assert G.edges[(5.0, 0.0), (10.0, 0.0)]["private_sewer"] is True
    assert G.edges[(5.0, 3.0), (5.0, 0.0)]["private_sewer"] is True
    assert not G.edges[(10.0, 0.0), (20.0, 0.0)].get("private_sewer")


def test_canonical_order_ignores_row_order():
    gdf = gpd.read_file(BUILDINGS)
    shuffled = gdf.sample(frac=1, random_state=1)
    a = pysewer.preprocessing.canonical_order(gdf)
    b = pysewer.preprocessing.canonical_order(shuffled)
    assert a.geometry.equals(b.geometry)
    assert list(a.index) == list(range(len(a)))


def _layout(roads, buildings):
    md = _domain(roads, buildings)
    md.add_sink(SINK)
    tree = rsph_tree(md.generate_connection_graph(), [SINK])
    return {
        tuple(sorted((tuple(round(c, 2) for c in u), tuple(round(c, 2) for c in v))))
        for u, v in tree.edges
    }


def test_network_does_not_depend_on_row_order():
    roads = gpd.read_file(ROADS)
    buildings = gpd.read_file(BUILDINGS)
    reference = _layout(roads, buildings)
    for seed in (1, 2):
        assert (
            _layout(
                roads.sample(frac=1, random_state=seed),
                buildings.sample(frac=1, random_state=seed),
            )
            == reference
        )
