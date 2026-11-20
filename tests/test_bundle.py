# SPDX-FileCopyrightText: 2025 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""NetworkBundle (contract bundle_v1): flat tables, metadata and profiles."""

import copy
import json
import math
import subprocess
import sys
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import pytest
from pyproj import CRS
from shapely.geometry import LineString

import pysewer
from pysewer.bundle import NODE_COLUMNS, PIPE_COLUMNS, NetworkBundle
from pysewer.helper import get_sewer_info

TEST_DATA = Path("tests") / "test_data"
SINK = (691350, 2553250)

# synthetic network, EPSG:32633 (x = 500000 is the central meridian, 15 E)
A = (500000, 5000000)  # building with a private pump
B = (500060, 5000080)  # lifting station
P = (500060, 5000200)  # pumping station
S = (500200, 5000200)  # wwtp
K = (500250, 5000200)  # building, undesigned house connection
ISO = (500300, 5000300)  # isolated building, onsite, unrouted
J = (500500, 5000500)  # isolated junction, dropped


def _pairs(*points):
    return [(float(s), float(z)) for s, z in points]


def _synthetic():
    G = nx.DiGraph(crs=CRS("EPSG:32633"))
    G.graph["unrouted_terminals"] = [ISO]
    G.graph["station_aware"] = {"iterations": [{"cost": 1.0}], "best": 0}
    G.add_node(A, node_type="building", elevation=110.0, pumping_station=True,
               total_static_head=3.0, upstream_pe=6, peak_flow=0.001, population=6)
    G.add_node(B, node_type=None, elevation=106.0, lifting_station=True,
               total_static_head=1.2, upstream_pe=6.0, peak_flow=0.001)
    G.add_node(P, node_type=None, elevation=104.0, pumping_station=True,
               total_static_head=0.5, upstream_pe=8, peak_flow=0.002)
    G.add_node(S, node_type="wwtp", elevation=102.0, upstream_pe=12, peak_flow=0.003)
    G.add_node(K, node_type="building", elevation=103.0)
    G.add_node(ISO, node_type="building", elevation=101.0, onsite=True)
    G.add_node(J, node_type=None, elevation=99.0)
    # added out of order on purpose (ids and rows must be sorted)
    G.add_edge(K, S, geometry=LineString([K, S]))
    G.add_edge(
        P, S,
        geometry=LineString([P, S]),
        profile=_pairs((0, 104), (70, 103), (140, 102)),
        trench_depth_profile=_pairs((0, 102.9), (70, 102.2), (140, 101.5)),
        diameter=0.3, pressurized=False, slope=0.014, peak_flow=0.003,
        velocity=0.9, d_over_D=0.4, mean_td=1.3,
    )
    G.add_edge(
        A, B,
        geometry=LineString([A, (500060, 5000000), B]),
        profile=_pairs((0, 110), (60, 109), (140, 106)),
        trench_depth_profile=_pairs((0, 108.5), (60, 107.5), (140, 104.0)),
        diameter=0.2, pressurized=False, slope=0.03, peak_flow=0.001,
        velocity=0.7, d_over_D=0.3, mean_td=1.75, private_sewer=True,
        hydraulic_violations=["max_velocity", "min_slope"],
        constraint_violations=["min_slope", "max_depth"],
        name=["Main St", "Side Rd"], highway="residential",
    )
    G.add_edge(
        B, P,
        geometry=LineString([B, P]),
        profile=_pairs((0, 106), (120, 104)),
        trench_depth_profile=_pairs((0, 105.2), (120, 103.2)),
        diameter=0.1, pressurized=True, slope=0.01, peak_flow=0.002,
        velocity=1.1, d_over_D=1.0, mean_td=0.8,
    )
    return G


@pytest.fixture
def G():
    return _synthetic()


@pytest.fixture
def b(G):
    return NetworkBundle.from_graph(G, name="syn")


def _pipe(b, pid):
    return b.pipes.set_index("pipe_id").loc[pid]


def _node(b, nid):
    return b.nodes.set_index("node_id").loc[nid]


# ------------------------------------------------------------------ ids, order
def test_columns_and_counts(b):
    assert list(b.pipes.columns) == [*PIPE_COLUMNS, "geometry"]
    assert list(b.nodes.columns) == [*NODE_COLUMNS, "geometry"]
    assert len(b.pipes) == 4
    assert len(b.nodes) == 6  # J (isolated junction) dropped, ISO (building) kept
    assert b.metadata["counts"] == {"pipes": 4, "nodes": 6}
    assert b.name == "syn"
    assert b.crs == CRS("EPSG:32633")
    assert b.pipes.crs == b.nodes.crs


def test_node_ids_sorted_by_rounded_xy(b):
    # sorted by (x, y): A, B, P, S, K, ISO (J dropped, sorts last)
    ids = dict(zip(b.nodes.geometry.apply(lambda g: (g.x, g.y)), b.nodes.node_id, strict=False))
    assert ids == {A: "N000001", B: "N000002", P: "N000003",
                   S: "N000004", K: "N000005", ISO: "N000006"}


def test_pipe_ids_sorted_by_from_to(b):
    # N1->N2, N2->N3, N3->N4, N5->N4
    assert list(b.pipes.pipe_id) == ["P000001", "P000002", "P000003", "P000004"]
    assert list(zip(b.pipes.from_node, b.pipes.to_node, strict=False)) == [
        ("N000001", "N000002"), ("N000002", "N000003"),
        ("N000003", "N000004"), ("N000005", "N000004")]


# ------------------------------------------------------------------ pipe values
def test_gravity_pipe_values(b):
    p = _pipe(b, "P000001")
    assert p.pipe_type == "gravity"
    assert p.private is True or p.private == True  # noqa: E712
    assert p.length == pytest.approx(140.0, abs=1e-9)  # 60 + 80
    assert p.diameter == 0.2
    assert p.ground_up == 110.0 and p.ground_down == 106.0
    assert p.invert_up == 108.5 and p.invert_down == 104.0
    assert p.depth_up == pytest.approx(1.5, abs=1e-9)  # 110 - 108.5
    assert p.depth_down == pytest.approx(2.0, abs=1e-9)  # 106 - 104
    assert p.crown_up == pytest.approx(108.7, abs=1e-9)  # 108.5 + 0.2
    assert p.crown_down == pytest.approx(104.2, abs=1e-9)  # 104 + 0.2
    # (104 - 108.5) / 140 = -4.5 / 140: negative = falling
    assert p.invert_slope == pytest.approx(-0.032142857142857, abs=1e-12)
    assert p.ground_slope == 0.03
    assert p.mean_td == 1.75
    assert (p.peak_flow, p.velocity, p.d_over_D) == (0.001, 0.7, 0.3)
    assert p.road_name == "Main St; Side Rd"
    assert p.highway == "residential"


def test_violations_strings_and_union_count(b):
    p = _pipe(b, "P000001")
    assert p.hydraulic_violations == "max_velocity,min_slope"
    assert p.constraint_violations == "min_slope,max_depth"
    assert p.n_violations == 3  # {max_velocity, min_slope, max_depth}
    q = _pipe(b, "P000003")
    assert q.hydraulic_violations == "" and q.constraint_violations == ""
    assert q.n_violations == 0


def test_second_gravity_pipe_slope_exact(b):
    p = _pipe(b, "P000003")
    assert p.length == pytest.approx(140.0, abs=1e-9)
    # (101.5 - 102.9) / 140 = -1.4 / 140 = -0.01
    assert p.invert_slope == pytest.approx(-0.01, abs=1e-12)
    assert p.crown_up == pytest.approx(103.2, abs=1e-9)  # 102.9 + 0.3
    assert p.crown_down == pytest.approx(101.8, abs=1e-9)  # 101.5 + 0.3
    assert p.depth_up == pytest.approx(1.1, abs=1e-9)  # 104 - 102.9
    assert p.road_name == "" and p.highway == ""
    assert p.private == False  # noqa: E712


def test_pressurized_pipe(b, G):
    p = _pipe(b, "P000002")
    assert p.pipe_type == "pressurized"
    assert p.length == pytest.approx(120.0, abs=1e-9)
    assert p.invert_up == 105.2 and p.invert_down == 103.2
    assert p.depth_up == pytest.approx(0.8, abs=1e-9)  # 106 - 105.2
    assert p.depth_down == pytest.approx(0.8, abs=1e-9)  # 104 - 103.2
    assert p.crown_up == pytest.approx(105.3, abs=1e-9)  # 105.2 + 0.1
    # (103.2 - 105.2) / 120 = -2 / 120
    assert p.invert_slope == pytest.approx(-0.016666666667, abs=1e-12)
    assert len(b.profiles["P000002"]["invert"]) == 2


def test_undesigned_pipe(b):
    p = _pipe(b, "P000004")
    assert p.pipe_type == "undesigned"
    assert p.length == pytest.approx(50.0, abs=1e-9)
    for col in ("diameter", "peak_flow", "velocity", "d_over_D", "ground_up",
                "invert_up", "depth_up", "crown_up", "invert_slope",
                "ground_slope", "mean_td"):
        assert math.isnan(p[col]), col
    assert p.n_violations == 0
    assert "P000004" not in b.profiles


def test_pipe_length_is_geometry_length(b, G):
    assert np.allclose(b.pipes["length"], b.pipes.geometry.length, atol=1e-9)


def test_pipe_geometry_direction_follows_flow(b):
    g = _pipe(b, "P000001").geometry
    assert next(iter(g.coords)) == A and tuple(g.coords)[-1] == B


# ------------------------------------------------------------------ node values
def test_roles(b):
    roles = dict(zip(b.nodes.node_id, b.nodes.role, strict=False))
    assert roles == {
        "N000001": "building",  # building + pump -> building
        "N000002": "lifting_station",
        "N000003": "pumping_station",
        "N000004": "sink",
        "N000005": "building",
        "N000006": "building",
    }


def test_private_pump_and_flags(b):
    a = _node(b, "N000001")
    assert a.private_pump == True and a.pumping_station == True  # noqa: E712
    assert a.lifting_station == False  # noqa: E712
    assert a.static_head == 3.0
    assert a.population == 6.0
    assert _node(b, "N000003").private_pump == False  # noqa: E712
    assert _node(b, "N000003").static_head == 0.5
    assert _node(b, "N000002").lifting_station == True  # noqa: E712
    assert _node(b, "N000002").static_head == 1.2
    assert math.isnan(_node(b, "N000004").static_head)  # stations only


def test_node_invert_is_min_incident(b):
    # A: only e1 up = 108.5
    assert _node(b, "N000001").invert == 108.5
    # B (lifting, invert jump): min(104.0 [e1 down], 105.2 [e2 up]) = 104.0
    assert _node(b, "N000002").invert == 104.0
    # P: min(103.2 [e2 down], 102.9 [e3 up]) = 102.9
    assert _node(b, "N000003").invert == 102.9
    # S: e3 down = 101.5 (e4 undesigned, NaN)
    assert _node(b, "N000004").invert == 101.5
    # K, ISO: no designed incident pipe
    assert math.isnan(_node(b, "N000005").invert)
    assert math.isnan(_node(b, "N000006").invert)


def test_node_depth_ground_and_types(b):
    assert _node(b, "N000002").ground == 106.0
    assert _node(b, "N000002").depth == pytest.approx(2.0, abs=1e-9)  # 106 - 104
    assert _node(b, "N000003").depth == pytest.approx(1.1, abs=1e-9)  # 104 - 102.9
    assert _node(b, "N000004").depth == pytest.approx(0.5, abs=1e-9)  # 102 - 101.5
    assert math.isnan(_node(b, "N000005").depth)
    assert _node(b, "N000002").node_type == ""  # None -> ""
    assert _node(b, "N000004").node_type == "wwtp"
    assert _node(b, "N000004").peak_flow == 0.003


def test_upstream_pe_nullable_int(b):
    assert str(b.nodes.upstream_pe.dtype) == "Int64"
    assert _node(b, "N000002").upstream_pe == 6
    assert _node(b, "N000004").upstream_pe == 12
    assert b.nodes.set_index("node_id").upstream_pe.isna()["N000005"]


def test_isolated_building_onsite_unrouted_kept(b):
    n = _node(b, "N000006")
    assert n.onsite == True and n.unrouted == True  # noqa: E712
    assert n.role == "building"
    assert math.isnan(n.invert)
    assert not b.nodes.drop(b.nodes.index[b.nodes.node_id == "N000006"]).unrouted.any()
    assert b.metadata["unrouted_terminals"] == ["N000006"]


def test_isolated_non_building_dropped(b):
    coords = {(g.x, g.y) for g in b.nodes.geometry}
    assert J not in coords


def test_node_geometry_from_key(b):
    n = b.nodes.set_index("node_id").geometry
    assert (n["N000003"].x, n["N000003"].y) == P


# --------------------------------------------------------------------- sinks
def test_default_sink_is_wwtp(b):
    assert b.metadata["sinks"] == ["N000004"]
    assert (b.nodes.role == "sink").sum() == 1


def test_explicit_sinks_set_role_and_metadata(G):
    b2 = NetworkBundle.from_graph(G, sinks=[P])
    assert _node(b2, "N000003").role == "sink"
    assert b2.metadata["sinks"] == ["N000003"]
    assert _node(b2, "N000001").role == "building"


def test_default_sink_without_wwtp_is_outdegree_zero():
    G = nx.DiGraph(crs=CRS("EPSG:32633"))
    G.add_node(A, node_type="building", elevation=10.0)
    G.add_node(B, node_type=None, elevation=9.0)
    G.add_edge(A, B, geometry=LineString([A, B]))
    b = NetworkBundle.from_graph(G)
    assert b.metadata["sinks"] == ["N000002"]
    assert _node(b, "N000002").role == "sink"


def test_node_already_sink_stays_sink(G):
    # explicit sink that is also the wwtp: still exactly one sink
    b2 = NetworkBundle.from_graph(G, sinks=[S])
    assert b2.metadata["sinks"] == ["N000004"]
    assert (b2.nodes.role == "sink").sum() == 1


# ------------------------------------------------------------------ metadata
def test_metadata_json_and_keys(b):
    m = b.metadata
    json.dumps(m)
    assert m["schema_version"] == "1.0"
    assert m["name"] == "syn"
    assert m["crs_epsg"] == 32633
    assert "UTM" in m["crs_wkt"] or "32633" in m["crs_wkt"]
    assert m["units"] == {"length": "m", "elevation": "m", "diameter": "m",
                          "flow": "m3/s", "velocity": "m/s", "slope": "m/m"}
    assert m["station_aware"] == {"iterations": [{"cost": 1.0}], "best": 0}
    assert isinstance(m["config"], dict) and m["config"]
    assert isinstance(m["pysewer_version"], str) and m["pysewer_version"]
    assert m["created_at"].endswith("+00:00") or m["created_at"].endswith("Z")
    assert "source_crs_wkt" not in m


def test_provenance_merge(G):
    b2 = NetworkBundle.from_graph(
        G,
        inputs={
            "dem": {"source": "local", "path": Path("/data/dem.tif")},
            "roads": {"url": "http://x/roads"},
            "dem_source": "srtm",
            "bbox_wgs84": [1.0, 2.0, 3.0, 4.0],
        },
    )
    prov = b2.metadata["provenance"]
    json.dumps(b2.metadata)
    assert prov["inputs"]["dem"] == {"source": "local", "path": "/data/dem.tif", "url": None}
    assert prov["inputs"]["roads"] == {"source": None, "path": None, "url": "http://x/roads"}
    assert prov["inputs"]["buildings"] == {"source": None, "path": None, "url": None}
    assert prov["dem_source"] == "srtm"
    assert prov["fetched_at"] is None
    assert prov["bbox_wgs84"] == [1.0, 2.0, 3.0, 4.0]


def test_provenance_defaults_are_none(b):
    prov = b.metadata["provenance"]
    assert prov["dem_source"] is None and prov["bbox_wgs84"] is None
    assert set(prov["inputs"]) == {"dem", "roads", "buildings"}


# ------------------------------------------------------------------ profiles
def test_profiles_shape_and_floats(b):
    assert set(b.profiles) == {"P000001", "P000002", "P000003"}
    p1 = b.profiles["P000001"]
    assert p1["ground"] == [[0.0, 110.0], [60.0, 109.0], [140.0, 106.0]]
    assert p1["invert"] == [[0.0, 108.5], [60.0, 107.5], [140.0, 104.0]]
    assert b.profiles["P000002"]["ground"] == [[0.0, 106.0], [120.0, 104.0]]
    assert b.profiles["P000002"]["invert"] == [[0.0, 105.2], [120.0, 103.2]]
    for prof in b.profiles.values():
        for key in ("ground", "invert"):
            for s, z in prof[key]:
                assert type(s) is float and type(z) is float
    json.dumps(b.profiles)


def test_profiles_are_copies(G, b):
    b.profiles["P000001"]["ground"][0][1] = -1.0
    assert G[A][B]["profile"][0] == (0.0, 110.0)


# ------------------------------------------------------------- no mutation
def test_graph_not_mutated(G):
    before = copy.deepcopy(G)
    NetworkBundle.from_graph(G, inputs={"dem": {"path": Path("x")}}, sinks=[P])
    assert dict(G.graph) == dict(before.graph)
    assert {n: d for n, d in G.nodes(data=True)} == {n: d for n, d in before.nodes(data=True)}
    assert list(G.edges) == list(before.edges)
    for u, v, d in G.edges(data=True):
        assert d == before[u][v]


# ------------------------------------------------------------------ to_wgs84
def test_to_wgs84(b):
    w = b.to_wgs84()
    assert w is not b
    assert w.crs.to_epsg() == 4326 and w.nodes.crs.to_epsg() == 4326
    assert w.metadata["crs_epsg"] == 4326
    assert w.metadata["source_crs_wkt"] == b.metadata["crs_wkt"]
    assert w.metadata["crs_wkt"] != b.metadata["crs_wkt"]
    json.dumps(w.metadata)
    n = w.nodes.set_index("node_id").geometry
    # x = 500000 is the central meridian of zone 33 -> lon 15.0 exactly
    assert n["N000001"].x == pytest.approx(15.0, abs=1e-9)
    # northing 5 000 000 m -> lat ~45.15 N (lon, lat order: y is the latitude)
    assert 45.15 < n["N000001"].y < 45.16
    # 60 m east of the meridian at 45 N: ~0.00076 deg
    assert 15.0007 < n["N000002"].x < 15.0008
    first = next(iter(w.pipes.geometry.iloc[0].coords))
    assert first == pytest.approx((n["N000001"].x, n["N000001"].y), abs=1e-12)
    # nothing recomputed
    assert list(w.pipes["length"]) == list(b.pipes["length"])
    assert w.profiles == b.profiles
    assert list(w.pipes.pipe_id) == list(b.pipes.pipe_id)
    assert list(w.pipes.columns) == list(b.pipes.columns)


def test_to_wgs84_leaves_original_unchanged(b):
    wkt, epsg = b.metadata["crs_wkt"], b.metadata["crs_epsg"]
    x0 = b.nodes.geometry.iloc[0].x
    w = b.to_wgs84()
    w.metadata["name"] = "other"
    w.profiles["P000001"]["ground"][0][1] = -5.0
    assert b.crs == CRS("EPSG:32633")
    assert b.metadata["crs_wkt"] == wkt and b.metadata["crs_epsg"] == epsg
    assert b.metadata["name"] == "syn"
    assert "source_crs_wkt" not in b.metadata
    assert b.nodes.geometry.iloc[0].x == x0
    assert b.profiles["P000001"]["ground"][0][1] == 110.0


def test_no_crs_raises_on_wgs84():
    G = nx.DiGraph()
    G.add_node(A, node_type="building", elevation=1.0)
    G.add_node(B, node_type="wwtp", elevation=0.0)
    G.add_edge(A, B, geometry=LineString([A, B]))
    b = NetworkBundle.from_graph(G)
    assert b.crs is None
    assert b.metadata["crs_epsg"] is None
    with pytest.raises(ValueError):
        b.to_wgs84()


# ------------------------------------------------------------------ edge cases
@pytest.mark.parametrize("with_crs", [True, False])
def test_empty_graph(with_crs):
    G = nx.DiGraph(crs=CRS("EPSG:32633")) if with_crs else nx.DiGraph()
    b = NetworkBundle.from_graph(G)
    assert len(b.pipes) == 0 and len(b.nodes) == 0
    assert list(b.pipes.columns) == [*PIPE_COLUMNS, "geometry"]
    assert list(b.nodes.columns) == [*NODE_COLUMNS, "geometry"]
    assert b.profiles == {}
    assert b.metadata["counts"] == {"pipes": 0, "nodes": 0}
    assert b.metadata["sinks"] == []
    json.dumps(b.metadata)


def test_single_pipe_graph():
    G = nx.DiGraph(crs=CRS("EPSG:32633"))
    G.add_node(A, node_type="building", elevation=5.0)
    G.add_node(B, node_type="wwtp", elevation=4.0)
    G.add_edge(A, B, geometry=LineString([A, B]), diameter=0.2, pressurized=False,
               profile=_pairs((0, 5), (100, 4)),
               trench_depth_profile=_pairs((0, 4.0), (100, 3.0)))
    b = NetworkBundle.from_graph(G)
    assert len(b.pipes) == 1 and len(b.nodes) == 2
    p = b.pipes.iloc[0]
    assert p.pipe_id == "P000001"
    # length: hypot(60, 80) = 100; slope (3.0 - 4.0) / 100 = -0.01
    assert p.length == pytest.approx(100.0, abs=1e-9)
    assert p.invert_slope == pytest.approx(-0.01, abs=1e-12)
    assert p.depth_up == pytest.approx(1.0, abs=1e-9)  # 5 - 4


def test_nan_attributes_are_nan_not_error():
    G = nx.DiGraph(crs=CRS("EPSG:32633"))
    G.add_node(A, node_type="building", elevation=float("nan"))
    G.add_node(B, node_type="wwtp", elevation=4.0)
    G.add_edge(A, B, geometry=LineString([A, B]), diameter=float("nan"),
               pressurized=False, slope=float("nan"), name=float("nan"),
               highway=None)
    b = NetworkBundle.from_graph(G)
    p = b.pipes.iloc[0]
    assert math.isnan(p.diameter) and math.isnan(p.ground_slope)
    assert p.road_name == "" and p.highway == ""
    assert p.pipe_type == "gravity"
    assert math.isnan(_node(b, "N000001").ground)


# ------------------------------------------------------------ light import
def test_import_bundle_is_light():
    code = (
        "import sys, pysewer\n"
        "from pysewer.bundle import NetworkBundle\n"
        "bad = [m for m in ('matplotlib', 'osmnx') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ------------------------------------------------------------- real network
@pytest.fixture(scope="module")
def designed():
    md = pysewer.ModelDomain(
        TEST_DATA / "dem.tif",
        TEST_DATA / "roads_clipped.shp",
        TEST_DATA / "buildings_clipped.shp",
    )
    md.add_sink(SINK)
    cg = md.generate_connection_graph()
    sinks = md.get_sinks()
    return pysewer.route_and_design(cg, sinks, station_cost=500, max_iterations=2), sinks


@pytest.fixture(scope="module")
def real(designed):
    G, sinks = designed
    return NetworkBundle.from_graph(G, name="real", sinks=sinks)


def test_real_counts_consistent_with_graph(designed, real):
    G, _ = designed
    assert len(real.pipes) == G.number_of_edges() > 0
    assert real.metadata["counts"]["pipes"] == len(real.pipes)
    assert real.metadata["counts"]["nodes"] == len(real.nodes)
    assert real.pipes.pipe_id.is_unique and real.nodes.node_id.is_unique
    assert list(real.pipes.columns) == [*PIPE_COLUMNS, "geometry"]
    assert list(real.nodes.columns) == [*NODE_COLUMNS, "geometry"]
    json.dumps(real.metadata)
    assert real.metadata["sinks"] and (real.nodes.role == "sink").sum() == len(
        real.metadata["sinks"])


def test_real_matches_sewer_info(designed, real):
    G, _ = designed
    info = get_sewer_info(G)
    pt = real.pipes.pipe_type
    assert real.pipes["length"][pt == "gravity"].sum() == pytest.approx(
        info["Gravity Sewer Length [m]"], abs=1.0)
    assert real.pipes["length"][pt == "pressurized"].sum() == pytest.approx(
        info["Pressurized Sewer Length [m]"], abs=1.0)
    roles = real.nodes.role
    assert (roles == "pumping_station").sum() == info["Pumping Stations"]
    assert (roles == "lifting_station").sum() == info["Lifting Stations"]
    assert real.nodes.private_pump.sum() == info["Private Pumps"]
    assert (roles == "building").sum() == info["Total Buildings"]
    assert real.nodes.unrouted.sum() == info["Unconnected Buildings"]


def test_real_one_outgoing_pipe_per_non_sink(real):
    sink_ids = set(real.metadata["sinks"])
    out = real.pipes.groupby("from_node").size()
    assert (out == 1).all(), out[out != 1]
    with_pipes = set(real.pipes.from_node) | set(real.pipes.to_node)
    for nid in with_pipes - sink_ids:
        assert out.get(nid, 0) == 1, nid
    assert not (set(out.index) & sink_ids)


def test_real_pipe_endpoints_exist_in_nodes(real):
    ids = set(real.nodes.node_id)
    assert set(real.pipes.from_node) <= ids and set(real.pipes.to_node) <= ids


def test_real_depths_non_negative(real):
    for col in ("depth_up", "depth_down"):
        d = real.pipes[col].dropna()
        assert (d >= -1e-9).all(), (col, d.min())
    d = real.nodes.depth.dropna()
    assert (d >= -1e-9).all(), d.min()


def test_real_profiles_consistent(real):
    assert set(real.profiles) <= set(real.pipes.pipe_id)
    pipes = real.pipes.set_index("pipe_id")
    for pid, prof in real.profiles.items():
        ground = prof["ground"]
        assert ground[0][0] == 0.0
        assert ground[-1][0] == pytest.approx(pipes["length"][pid], rel=1e-6, abs=1e-6)
        assert ground[0][1] == pytest.approx(pipes.ground_up[pid])
        assert ground[-1][1] == pytest.approx(pipes.ground_down[pid])
        if prof["invert"]:
            assert prof["invert"][0][1] == pytest.approx(pipes.invert_up[pid])
            assert prof["invert"][-1][1] == pytest.approx(pipes.invert_down[pid])
            if pipes.pipe_type[pid] == "pressurized":
                assert len(prof["invert"]) == 2
            else:
                assert [s for s, _ in prof["invert"]] == [s for s, _ in ground]


def test_real_crown_and_wgs84(real):
    p = real.pipes.dropna(subset=["invert_up", "diameter"])
    assert np.allclose(p.crown_up, p.invert_up + p.diameter, atol=1e-9)
    assert real.crs is not None
    w = real.to_wgs84()
    xs = w.nodes.geometry.x
    ys = w.nodes.geometry.y
    assert xs.between(-180, 180).all() and ys.between(-90, 90).all()
    assert real.crs.is_projected


# ------------------------------------------------- amendments after the gate
def test_ids_dense_after_dropping_isolated_junction():
    G = _synthetic()
    G.add_node((500030, 5000030), node_type=None, elevation=1.0)  # sorts 2nd
    b = NetworkBundle.from_graph(G)
    assert sorted(b.nodes.node_id) == [f"N{i:06d}" for i in range(1, 7)]
    assert len(b.nodes) == 6
    assert b.metadata["counts"]["nodes"] == 6


def test_degree_zero_sink_and_unrouted_kept():
    G = _synthetic()
    iso_sink = (500400, 5000400)
    G.add_node(iso_sink, node_type="wwtp", elevation=1.0)
    G.graph["unrouted_terminals"] = [ISO, (500450, 5000450)]
    G.add_node((500450, 5000450), node_type=None, elevation=1.0)
    b = NetworkBundle.from_graph(G)
    ids = set(b.nodes.node_id)
    assert set(b.metadata["sinks"]) <= ids and len(b.metadata["sinks"]) == 2
    assert set(b.metadata["unrouted_terminals"]) <= ids
    assert len(b.metadata["unrouted_terminals"]) == 2
    assert len(b.nodes) == 8  # 6 + isolated sink + isolated unrouted
    assert b.metadata["sinks"] == sorted(b.metadata["sinks"])
    assert b.metadata["unrouted_terminals"] == sorted(b.metadata["unrouted_terminals"])


def test_unknown_explicit_sink_warns(G, caplog):
    with caplog.at_level("WARNING"):
        b = NetworkBundle.from_graph(G, sinks=[P, (1, 2)])
    assert any(r.levelname == "WARNING" for r in caplog.records)
    assert b.metadata["sinks"] == ["N000003"]


def test_flags_accept_numpy_bool_and_one():
    G = _synthetic()
    G.nodes[B]["lifting_station"] = np.True_
    G.nodes[P]["pumping_station"] = 1
    G.nodes[ISO]["onsite"] = np.True_
    G.edges[A, B]["private_sewer"] = np.True_
    b = NetworkBundle.from_graph(G)
    assert _node(b, "N000002").role == "lifting_station"
    assert _node(b, "N000003").role == "pumping_station"
    assert _node(b, "N000006").onsite == True  # noqa: E712
    assert _pipe(b, "P000001").private == True  # noqa: E712


def test_population_attribute_lookup_order():
    G = _synthetic()
    # A has population=6; block_population has priority over it
    G.nodes[A]["block_population"] = 9
    b = NetworkBundle.from_graph(G)
    assert _node(b, "N000001").population == 9.0
    assert b.metadata["population_attribute"] == "block_population"
    G2 = _synthetic()
    b2 = NetworkBundle.from_graph(G2)
    assert _node(b2, "N000001").population == 6.0
    assert b2.metadata["population_attribute"] == "population"
    assert math.isnan(_node(b2, "N000005").population)


def test_population_configured_attribute_wins(monkeypatch):
    from pysewer.config import manager
    opt = manager.get_config().optimization
    monkeypatch.setattr(opt, "inhabitants_dwelling_attribute_name", "Pers")
    G = _synthetic()
    G.nodes[A]["Pers"] = 3
    G.nodes[A]["block_population"] = 9
    b = NetworkBundle.from_graph(G)
    assert _node(b, "N000001").population == 3.0
    assert b.metadata["population_attribute"].lower() == "pers"


def test_no_population_attribute_gives_none():
    G = _synthetic()
    del G.nodes[A]["population"]
    b = NetworkBundle.from_graph(G)
    assert b.metadata["population_attribute"] is None
    assert b.nodes.population.isna().all()


def test_provenance_extra_keys():
    b = NetworkBundle.from_graph(_synthetic(), inputs={"note": "x", "dem_source": "s"})
    prov = b.metadata["provenance"]
    assert prov["extra"] == {"note": "x"}
    assert prov["dem_source"] == "s" and "note" not in prov
    json.dumps(b.metadata)


def test_dtypes_public_and_applied(b):
    from pysewer.bundle import NODE_DTYPES, PIPE_DTYPES, coerce_dtypes
    assert list(PIPE_DTYPES) == list(PIPE_COLUMNS)
    assert list(NODE_DTYPES) == list(NODE_COLUMNS)
    assert PIPE_DTYPES["length"] == "float64" and PIPE_DTYPES["private"] == "bool"
    assert PIPE_DTYPES["n_violations"] == "int64"
    for df, spec in ((b.pipes, PIPE_DTYPES), (b.nodes, NODE_DTYPES)):
        for col, dt in spec.items():
            if dt == "str":
                assert pd.api.types.is_string_dtype(df[col]), col
            else:
                assert str(df[col].dtype) == dt, (col, df[col].dtype)
    out = coerce_dtypes(pd.DataFrame({"a": [1, 2], "t": [None, "x"]}),
                        {"a": "float64", "t": "str", "zz": "bool"})
    assert str(out["a"].dtype) == "float64"
    assert list(out["t"]) == ["", "x"]


def test_empty_graph_has_typed_columns():
    from pysewer.bundle import NODE_DTYPES, PIPE_DTYPES
    b = NetworkBundle.from_graph(nx.DiGraph(crs=CRS("EPSG:32633")))
    for df, spec in ((b.pipes, PIPE_DTYPES), (b.nodes, NODE_DTYPES)):
        for col, dt in spec.items():
            if dt == "str":
                assert pd.api.types.is_string_dtype(df[col]), col
            else:
                assert str(df[col].dtype) == dt, (col, df[col].dtype)


def test_road_name_fallback_columns():
    G = _synthetic()
    G.edges[P, S]["ROADNAMEE"] = "Al Nahda"
    G.edges[P, S]["SUBTYPELBL"] = "Local"
    b = NetworkBundle.from_graph(G)
    assert _pipe(b, "P000003").road_name == "Al Nahda"
    assert _pipe(b, "P000003").highway == "Local"
