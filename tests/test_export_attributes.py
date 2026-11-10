# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""Merged edges keep their attributes, and exports write typed columns with
NULL for missing values instead of the strings "nan" and "True"."""

import shutil
from pathlib import Path

import fiona
import geopandas as gpd
import networkx as nx
import numpy as np
import pytest
from shapely.geometry import LineString, Point

import pysewer
from pysewer.export import export_sewer_network, write_gdf_to_gpkg, write_gdf_to_shp
from pysewer.helper import get_edge_gdf, get_node_keys
from pysewer.routing import rsph_tree
from pysewer.simplify import simplify_graph

TEST_DATA = Path("tests") / "test_data"
SINK = (691350, 2553250)


def test_simplify_keeps_attributes():
    G = nx.Graph()
    G.add_edge((0, 0), (1, 0), private_sewer=True, name="a", osm=np.nan)
    G.add_edge((1, 0), (2, 0), private_sewer=True, name="b", osm=7)
    G.add_edge((2, 0), (3, 0), private_sewer=True, name="a", osm=np.nan)
    S = simplify_graph(G)
    ((_, _, attrs),) = S.edges(data=True)
    assert attrs["private_sewer"] is True
    assert attrs["name"] == ["a", "b"]  # distinct values, in the order met
    assert attrs["osm"] == 7  # missing values are dropped
    assert attrs["geometry"].length == pytest.approx(3)


@pytest.fixture
def gdf():
    return gpd.GeoDataFrame(
        {
            "private_sewer": [True, np.nan, True],
            "violations": [["max_slope"], np.nan, []],
            "count": [1, 2, 3],
            "diameter": [0.2, np.nan, 0.3],
            "name": ["a", None, "c"],
        },
        geometry=[LineString([(0, i), (1, i)]) for i in range(3)],
        crs="EPSG:32640",
    )


@pytest.mark.parametrize("writer", ["gpkg", "shp"])
def test_export_types_and_nulls(gdf, tmp_path, writer):
    path = tmp_path / f"pipes.{writer}"
    if writer == "gpkg":
        write_gdf_to_gpkg(gdf, str(path), layer="pipes")
    else:
        write_gdf_to_shp(gdf, str(path))
    with fiona.open(path) as src:
        schema = src.schema["properties"]
        rows = [dict(f["properties"]) for f in src]
    short = {k[:10]: k for k in gdf.columns}  # shapefile field names are cut at 10
    types = {short.get(k, k): v.split(":")[0] for k, v in schema.items()}
    assert types["private_sewer"] in ("bool", "int")  # shp stores bools as int
    assert types["count"] == "int"
    assert types["diameter"] == "float"
    assert types["name"] == "str"
    values = [{short.get(k, k): v for k, v in r.items()} for r in rows]
    assert [bool(r["private_sewer"]) if r["private_sewer"] is not None else None
            for r in values] == [True, None, True]
    assert [r["violations"] for r in values] == ['["max_slope"]', None, "[]"]
    assert [r["name"] for r in values] == ["a", None, "c"]
    assert values[1]["diameter"] is None
    assert "nan" not in {str(v) for r in values for v in r.values()}


def test_parquet_mixed_list_column(tmp_path):
    gdf = gpd.GeoDataFrame(
        {"OBJECTID": [1, [2, 3], np.nan], "profile": [[(0.0, 1.0)], [(0.0, 2.0)], [(0.0, 3.0)]]},
        geometry=[LineString([(0, i), (1, i)]) for i in range(3)],
        crs="EPSG:32640",
    )
    path = tmp_path / "pipes.parquet"
    export_sewer_network(gdf, str(path), file_format="parquet")
    back = gpd.read_parquet(path)
    assert back["OBJECTID"].tolist()[:2] == ["1", "[2, 3]"]
    assert back["OBJECTID"].isna().tolist() == [False, False, True]
    assert back.crs == gdf.crs


def test_pipeline_export_flags_house_connections(tmp_path):
    shutil.copy(TEST_DATA / "dem.tif", tmp_path / "dem.tif")
    md = pysewer.ModelDomain(
        tmp_path / "dem.tif",
        TEST_DATA / "roads_clipped.shp",
        TEST_DATA / "buildings_clipped.shp",
    )
    md.add_sink(SINK)
    tree = rsph_tree(md.generate_connection_graph(), [SINK])
    buildings = set(get_node_keys(tree, field="node_type", value="building"))
    path = tmp_path / "network.gpkg"
    write_gdf_to_gpkg(get_edge_gdf(tree, detailed=True), str(path), layer="pipes")
    pipes = gpd.read_file(path, layer="pipes")
    assert set(pipes["private_sewer"].dropna()) <= {True, False}
    at_building = [
        (tuple(g.coords[0]) in buildings or tuple(g.coords[-1]) in buildings)
        for g in pipes.geometry
    ]
    flagged = pipes["private_sewer"].fillna(False).astype(bool).tolist()
    assert all(f for f, b in zip(flagged, at_building, strict=True) if b)
    assert not any(isinstance(v, str) for v in pipes["private_sewer"])


def test_split_road_edge_keeps_road_attributes():
    md = pysewer.ModelDomain(
        TEST_DATA / "dem.tif",
        TEST_DATA / "roads_clipped.shp",
        TEST_DATA / "buildings_clipped.shp",
        connect_buildings=False,
    )
    G = nx.Graph()
    G.add_edge((0.0, 0.0), (10.0, 0.0), ROADNO="R1", geometry=LineString([(0, 0), (10, 0)]))
    md.connect_to_roadnetwork(G, Point(5, 3), Point(5, 0), LineString([(0, 0), (10, 0)]))
    for half in [((0.0, 0.0), (5.0, 0.0)), ((5.0, 0.0), (10.0, 0.0))]:
        assert G.edges[half]["ROADNO"] == "R1"
        assert "geometry" not in G.edges[half]


def test_road_pipes_keep_road_attributes(tmp_path):
    shutil.copy(TEST_DATA / "dem.tif", tmp_path / "dem.tif")
    md = pysewer.ModelDomain(
        tmp_path / "dem.tif",
        TEST_DATA / "roads_clipped.shp",
        TEST_DATA / "buildings_clipped.shp",
    )
    md.add_sink(SINK)
    tree = rsph_tree(md.generate_connection_graph(), [SINK])
    pipes = get_edge_gdf(tree, detailed=True)
    road = pipes[pipes["private_sewer"].isna()]
    assert len(road) > 0
    assert road["ROADNO"].notna().all()
    assert "length" not in pipes.columns
