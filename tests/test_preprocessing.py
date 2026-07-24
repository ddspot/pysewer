# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import networkx as nx

# test dem
import pytest
from shapely.geometry import LineString, Point

import pysewer


@pytest.fixture
def dem_data():
    dem = pysewer.DEM(Path("tests") / "test_data" / "dem.tif")
    point1 = Point(691410, 2553010)
    profile1 = LineString([(691510, 2552770), (691510, 2552780), (691510, 2552785)])
    profileOOB = LineString([(691410, 2553010), (1091410, 2553010)])
    return dem, point1, profile1, profileOOB


def test_get_elevation(dem_data):
    dem, point1, _, _ = dem_data
    assert dem.get_elevation(point1) == 186.20


def test_get_elevation_type(dem_data):
    dem, point1, _, _ = dem_data
    assert isinstance(dem.get_elevation(point1), float)


def test_get_elevation_out_of_bounds(dem_data):
    dem, _, _, _ = dem_data
    with pytest.raises(ValueError):
        dem.get_elevation(Point(0, 0))


def test_get_profile_sample_spacing(dem_data):
    dem, _, profile1, _ = dem_data
    profile = dem.get_profile(profile1)
    x = [p[0] for p in profile]
    assert x == [0, 10, 15]
    profile = dem.get_profile(profile1, dx=15)
    x = [p[0] for p in profile]
    assert x == [0, 15]


def test_get_profile(dem_data):
    dem, _, profile1, _ = dem_data
    profile = dem.get_profile(profile1)
    y = [p[1] for p in profile]
    assert y == [181.37, 179.56, 177.41]


def test_get_crs_dem():
    dem = pysewer.DEM(Path("tests") / "test_data" / "dem.tif")
    assert dem.get_crs.to_authority() == ("EPSG", "32640")


@pytest.fixture
def buildings():
    roads = pysewer.Roads(Path("tests") / "test_data" / "roads_clipped.shp")
    return pysewer.Buildings(
        Path("tests") / "test_data" / "buildings_clipped.shp", roads_obj=roads
    )


@pytest.fixture
def roads():
    return pysewer.Roads(Path("tests") / "test_data" / "roads_clipped.shp")


# for testing the connection graph
@pytest.fixture
def connection_graph():
    sink_coordinates = [(691350, 2553250)]
    dem = Path("tests") / "test_data" / "dem.tif"
    buildings = Path("tests") / "test_data" / "buildings_clipped.shp"
    roads = Path("tests") / "test_data" / "roads_clipped.shp"
    model_domain = pysewer.ModelDomain(dem, roads, buildings)
    model_domain.add_sink(sink_coordinates)
    connection_graph = model_domain.generate_connection_graph()
    return connection_graph


# def test_get_crs(buildings, roads):
#     assert buildings.get_crs() == "epsg:32640"
#     assert roads.get_crs() == "epsg:32640"


def test_gdf_length(buildings, roads):
    assert len(buildings.get_gdf()) == 57
    assert len(roads.get_gdf()) == 8


def test_num_buildings(connection_graph):
    buildings_in_conn_graph = len(
        pysewer.get_node_keys(connection_graph, field="node_type", value="building")
    )
    assert buildings_in_conn_graph == 57


def test_wwtp_location(connection_graph):
    sink_coordinates = (691350, 2553250)
    wwtp_loc = pysewer.get_node_keys(connection_graph, field="node_type", value="wwtp")[
        0
    ]
    assert wwtp_loc == sink_coordinates


def test_connectivity(connection_graph):
    wwtp_loc = pysewer.get_node_keys(connection_graph, field="node_type", value="wwtp")[
        0
    ]
    for b in pysewer.get_node_keys(
        connection_graph, field="node_type", value="building"
    ):
        assert nx.has_path(connection_graph, b, wwtp_loc)


def test_edge_data_geometry(connection_graph):
    test_u = (691535.5977087952, 2553057.6150246616)
    test_v = (691567.7330147347, 2552952.9508892866)
    assert isinstance(connection_graph[test_u][test_v][0]["geometry"], LineString)


def test_edge_data_profile(connection_graph):
    test_u = (691535.5977087952, 2553057.6150246616)
    test_v = (691567.7330147347, 2552952.9508892866)
    profile = [
        (0.0, 143.33679),
        (10.0, 143.68501),
        (20.0, 141.83961),
        (30.0, 140.2519),
        (40.0, 136.73189),
        (50.0, 136.25243),
        (60.0, 136.14067),
        (70.0, 134.04604),
        (80.0, 134.00385),
        (90.0, 133.05428),
        (100.0, 133.5003),
        (110.0, 133.38301),
        (112.76465613018571, 133.99133),
    ]
    y_r = [round(p[1], 2) for p in profile]
    y_t = [round(p[1], 2) for p in connection_graph[test_u][test_v][0]["profile"]]
    assert y_t == y_r


def test_edge_data_needs_pump(connection_graph):
    test_u = (691535.5977087952, 2553057.6150246616)
    test_v = (691567.7330147347, 2552952.9508892866)
    assert not connection_graph[test_u][test_v][0]["needs_pump"]
    test_u = (691384.1703000003, 2553067.1856999993)
    test_v = (691386.9481000002, 2553059.71885)
    # check if node exists
    if test_u in connection_graph and test_v in connection_graph:
        assert connection_graph[test_u][test_v][0]["needs_pump"]


def test_pump_penalty(connection_graph):
    test_u = (691384.1703000003, 2553067.1856999993)
    test_v = (691386.9481000002, 2553059.71885)
    # default pump penalty is 1000
    assert connection_graph[test_u][test_v][0]["weight"] == 7966.807500749309

    # set pump penalty to 1
    model_domain = pysewer.ModelDomain(
        Path("tests") / "test_data" / "dem.tif",
        Path("tests") / "test_data" / "roads_clipped.shp",
        Path("tests") / "test_data" / "buildings_clipped.shp",
    )
    model_domain.add_sink([(691350, 2553250)])
    model_domain.set_pump_penalty(1)
    connection_graph = model_domain.generate_connection_graph()
    assert connection_graph[test_u][test_v][0]["weight"] == 7966.807500749309 / 1000
