# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""Sewer lengths follow the pipes, outputs carry the CRS, and pysewer logs instead of printing."""

import logging
import shutil
from pathlib import Path

import geopandas as gpd
import networkx as nx
import pytest
from shapely.geometry import LineString

import pysewer
from pysewer.export import export_sewer_network, write_gdf_to_gpkg, write_gdf_to_shp
from pysewer.helper import get_edge_gdf, get_node_gdf, get_sewer_info
from pysewer.optimization import calculate_hydraulic_parameters, estimate_peakflow
from pysewer.routing import rsph_tree

TEST_DATA = Path("tests") / "test_data"
SINK = (691350, 2553250)


# ---------------------------------------------------------------- lengths
def _two_pipes(graph_cls=nx.DiGraph):
    G = graph_cls()
    # 3-4-5 detour: the pipe is 7 m long, its end points 5 m apart
    G.add_edge((0, 0), (3, 4), geometry=LineString([(0, 0), (3, 0), (3, 4)]), pressurized=False)
    # no geometry: falls back to the straight line (6 m)
    G.add_edge((3, 4), (3, 10), pressurized=True)
    return G


def test_lengths_follow_pipe_geometry():
    # 0.3.1 measured the straight line between the end nodes: 5 m
    info = get_sewer_info(_two_pipes())
    assert info["Gravity Sewer Length [m]"] == 7
    assert info["Pressurized Sewer Length [m]"] == 6


def test_lengths_on_a_multigraph():
    info = get_sewer_info(_two_pipes(nx.MultiDiGraph))
    assert info["Gravity Sewer Length [m]"] == 7


# ------------------------------------------------------------ full pipeline
@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pipeline")
    shutil.copy(TEST_DATA / "dem.tif", tmp / "dem.tif")
    md = pysewer.ModelDomain(
        tmp / "dem.tif", TEST_DATA / "roads_clipped.shp", TEST_DATA / "buildings_clipped.shp"
    )
    md.add_sink(SINK)
    G = md.generate_connection_graph()
    t = rsph_tree(G, [SINK])
    estimate_peakflow(t, inhabitants_dwelling=3, daily_wastewater_person=0.15)
    calculate_hydraulic_parameters(t, sinks=[SINK])
    return md, G, t


def test_sewer_length_equals_sum_of_pipe_geometries(tree):
    _, _, t = tree
    info = get_sewer_info(t)
    along = sum(d["geometry"].length for *_, d in t.edges(data=True))
    total = info["Gravity Sewer Length [m]"] + info["Pressurized Sewer Length [m]"]
    assert abs(total - along) <= 1


# -------------------------------------------------------------------- CRS
def test_graphs_and_gdfs_carry_crs(tree):
    md, G, t = tree
    for graph in (md.connection_graph, G, t):
        assert graph.graph["crs"].to_epsg() == 32640
    assert get_edge_gdf(t, detailed=True).crs.to_epsg() == 32640
    assert get_edge_gdf(t).crs.to_epsg() == 32640
    assert get_node_gdf(t).crs.to_epsg() == 32640


@pytest.mark.parametrize("fmt", ["gpkg", "shp", "parquet"])
def test_exports_carry_crs(tree, tmp_path, fmt):
    # 0.3.1 wrote gpkg and shp layers without a CRS
    _, _, t = tree
    path = tmp_path / f"pipes.{fmt}"
    export_sewer_network(get_edge_gdf(t, detailed=True), str(path), file_format=fmt)
    read = gpd.read_parquet(path) if fmt == "parquet" else gpd.read_file(path)
    assert read.crs.to_epsg() == 32640


def test_explicit_crs_argument_wins(tree, tmp_path):
    _, _, t = tree
    gdf = get_edge_gdf(t, detailed=True)
    write_gdf_to_gpkg(gdf, str(tmp_path / "a.gpkg"), crs="EPSG:32640")
    assert gpd.read_file(tmp_path / "a.gpkg").crs.to_epsg() == 32640
    write_gdf_to_shp(gdf.set_crs(None, allow_override=True), str(tmp_path / "b.shp"))
    assert gpd.read_file(tmp_path / "b.shp").crs is None


# ---------------------------------------------------------------- logging
def test_pipeline_prints_nothing(tmp_path, capsys, caplog):
    shutil.copy(TEST_DATA / "dem.tif", tmp_path / "dem.tif")
    with caplog.at_level(logging.INFO, logger="pysewer"):
        md = pysewer.ModelDomain(
            tmp_path / "dem.tif", TEST_DATA / "roads_clipped.shp", TEST_DATA / "buildings_clipped.shp"
        )
        md.add_sink(SINK)
        t = rsph_tree(md.generate_connection_graph(), [SINK])
        estimate_peakflow(t, inhabitants_dwelling=3, daily_wastewater_person=0.15)
        calculate_hydraulic_parameters(t, sinks=[SINK])
        export_sewer_network(get_edge_gdf(t, detailed=True), str(tmp_path / "p.gpkg"))
    # 0.3.1 printed a line per connection-graph edge and several summaries
    assert capsys.readouterr().out == ""
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "Connection graph:" in messages
    assert "Sewer graph:" in messages
    # the per-edge lines are debug, not info
    assert not any(
        r.levelno >= logging.INFO and "needs pump. Distance" in r.getMessage()
        for r in caplog.records
    )
