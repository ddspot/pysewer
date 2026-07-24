# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

import geopandas as gpd
import networkx as nx
import pytest

from pysewer.helper import (
    get_edge_keys,
    get_node_keys,
    remove_third_dimension,
)


@pytest.fixture
def graph():
    G = nx.DiGraph()
    G.add_edge(1, 2, weight=7, geometry="LINESTRING(0 0, 1 1)")
    G.add_edge(2, 3, weight=5, geometry="LINESTRING(1 1, 2 2)")
    G.add_edge(1, 3, weight=10, geometry="LINESTRING(0 0, 2 2)")
    return G


@pytest.fixture
def gdf():
    data = {"id": [1, 2, 3], "geometry": ["POINT(0 0)", "POINT(1 1)", "POINT(2 2)"]}
    return gpd.GeoDataFrame(data, crs="EPSG:4326")


def test_get_node_keys(graph):
    assert get_node_keys(graph) == [1, 2, 3]


def test_get_edge_keys(graph):
    assert get_edge_keys(graph, field="weight", value=7) == [(1, 2)]


# def test_get_path_gdf(graph):
#     gdf = get_path_gdf(graph, 1, 3)
#     assert len(gdf) == 2
#     assert gdf.geometry.iloc[0].is_valid
#     assert gdf.geometry.iloc[1].is_valid
#     # assert gdf.geometry.iloc[0].wkt == "LINESTRING (0 0, 1 1)"
#     # assert gdf.geometry.iloc[1].wkt == "LINESTRING (1 1, 2 2)"


# def test_get_mean_slope(graph):
#     assert get_mean_slope(graph, 1, 3, 0, 2) == 1.0


# def test_ckdnearest(gdf):
#     gdfA = gdf.copy()
#     gdfB = gdf.copy()
#     gdfB.geometry = gdfB.geometry.transpose(xoff=1.0, yoff=1.0)
#     result = ckdnearest(gdfA, gdfB)
#     assert len(result) == 3
#     assert result.iloc[0]["id"] == 1
#     assert result.iloc[0]["closest_edge"].wkt == "LINESTRING (1 1, 2 2)"


def test_remove_third_dimension():
    from shapely.geometry import LineString, Point, Polygon

    p = Point(0, 0, 1)
    assert remove_third_dimension(p).wkt == "POINT (0 0)"

    l = LineString([(0, 0, 1), (1, 1, 2)])
    assert remove_third_dimension(l).wkt == "LINESTRING (0 0, 1 1)"

    poly = Polygon([(0, 0, 1), (1, 1, 2), (2, 0, 3)])
    assert remove_third_dimension(poly).wkt == "POLYGON ((0 0, 1 1, 2 0, 0 0))"