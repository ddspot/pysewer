# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
import itertools
from pathlib import Path

import networkx as nx

# test dem
import pytest
from shapely.geometry import LineString, Point

import pysewer
from pysewer.helper import dedupe_coords, points_close


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


def test_get_profile_warns_on_implausible_gradient(tmp_path):
    """A DEM with UNtagged nodata (0.0 border cells in high terrain) must
    not pass silently: the fabricated cliff gradient triggers a warning
    (Wasweta II benchmark, 2026-08-08 — 1,387 m drop over 33 m of pipe)."""
    import warnings

    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "dem_untagged_nodata.tif"
    data = np.full((10, 10), 1400.0, dtype="float32")
    data[:, -1] = 0.0  # nodata border, missing from the nodata tag
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=10,
        width=10,
        count=1,
        dtype="float32",
        crs="EPSG:32736",
        transform=from_origin(0, 100, 10, 10),
    ) as dst:
        dst.write(data, 1)

    dem = pysewer.DEM(path)
    with pytest.warns(UserWarning, match="gradient"):
        dem.get_profile(LineString([(5, 55), (95, 55)]))
    # a profile that stays on valid terrain must stay silent
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        dem.get_profile(LineString([(5, 55), (75, 55)]))


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


def _node_near(G, xy, tol=1e-3):
    """The node within ``tol`` m of ``xy`` (split points carry float noise)."""
    return next(n for n in G.nodes if abs(n[0] - xy[0]) < tol and abs(n[1] - xy[1]) < tol)


def _test_edge(G):
    return (
        _node_near(G, (691535.5977087952, 2553057.6150246616)),
        _node_near(G, (691567.7330147347, 2552952.9508892866)),
    )


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
    test_u, test_v = _test_edge(connection_graph)
    assert isinstance(connection_graph[test_u][test_v][0]["geometry"], LineString)


def test_edge_data_profile(connection_graph):
    test_u, test_v = _test_edge(connection_graph)
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


def _pump_edge(connection_graph):
    """Some edge flagged needs_pump (which one depends on the layout, not the test)."""
    for u, v, data in connection_graph.edges(data=True):
        if data.get("needs_pump"):
            return u, v
    raise AssertionError("expected at least one needs_pump edge in the test network")


def test_edge_data_needs_pump(connection_graph):
    test_u, test_v = _test_edge(connection_graph)
    assert not connection_graph[test_u][test_v][0]["needs_pump"]
    _pump_edge(connection_graph)  # the test terrain has uphill edges


def test_pump_penalty(connection_graph):
    u, v = _pump_edge(connection_graph)
    distance = connection_graph[u][v][0]["distance"]
    # default pump penalty is 1000; gravity edges are weighted by distance only
    assert connection_graph[u][v][0]["weight"] == pytest.approx(distance * 1000)
    gravity = next(d for _, _, d in connection_graph.edges(data=True) if not d.get("needs_pump"))
    assert gravity["weight"] == pytest.approx(gravity["distance"])

    # set pump penalty to 1
    model_domain = pysewer.ModelDomain(
        Path("tests") / "test_data" / "dem.tif",
        Path("tests") / "test_data" / "roads_clipped.shp",
        Path("tests") / "test_data" / "buildings_clipped.shp",
    )
    model_domain.add_sink([(691350, 2553250)])
    model_domain.set_pump_penalty(1)
    connection_graph = model_domain.generate_connection_graph()
    assert connection_graph[u][v][0]["weight"] == pytest.approx(distance)


# --- issue #4: near-duplicate vertices / zero-length pipe geometries ---


def test_dedupe_coords_removes_float_noise_duplicate():
    # two vertices differing only at ~1e-10 (issue #4's reported artifact)
    a = (707752.93644460884, 1600963.52702944283)
    b = (707752.93644460896, 1600963.52702944260)
    c = (707800.0, 1600990.0)
    out = dedupe_coords([a, b, c])
    assert out == [a, c]


def test_dedupe_coords_preserves_distinct_vertices():
    pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    assert dedupe_coords(pts) == pts


def test_dedupe_coords_keeps_two_endpoints_when_all_collapse():
    # a degenerate all-coincident path must still yield a constructible line
    p = (5.0, 5.0)
    out = dedupe_coords([p, p, p])
    assert len(out) == 2


def test_points_close_tolerance():
    assert points_close((0, 0), (0, 1e-9))
    assert not points_close((0, 0), (0, 1.0))


def test_dedupe_reproduces_degenerate_split_geometry():
    """Reproduce the artifact of issue #4 at the geometry-construction step: a
    connection point landing on an existing road vertex (buildings pre-snapped
    onto road vertices) leaves a graph path with a duplicate/near-duplicate
    node. Building the pipe LineString from that path must not yield a
    zero-length segment or a non-monotonic vertex sequence."""
    u = (707752.93644460884, 1600963.52702944283)
    # a connection point projected onto u, offset only by float noise
    cp = (707752.93644460896, 1600963.52702944260)
    v = (707800.0, 1600990.0)
    # raw shortest path through the degenerate split: u -> cp(~u) -> v
    raw_path = [u, cp, v]
    geom = LineString(dedupe_coords(raw_path))
    # the near-duplicate vertex is gone; distance is strictly increasing
    coords = list(geom.coords)
    assert coords == [u, v]
    seglen = [
        LineString([a, b]).length for a, b in itertools.pairwise(coords)
    ]
    assert all(s > 0 for s in seglen)


def test_no_degenerate_pipe_geometries(connection_graph):
    """Issue #4: every pipe LineString has strictly increasing curvilinear
    distance — no zero-length pipes, no float-noise duplicate vertices."""
    for u, v, data in connection_graph.edges(data=True):
        geom = data.get("geometry")
        if geom is None:
            continue
        assert geom.length > 0, f"zero-length pipe {u}->{v}"
        coords = list(geom.coords)
        for a, b in itertools.pairwise(coords):
            assert not points_close(a, b), f"near-duplicate vertex in pipe {u}->{v}"
