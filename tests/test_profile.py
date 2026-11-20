# SPDX-FileCopyrightText: 2025 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""Longitudinal profile (contract profile/1): trace, arrays, JSON round trip."""

import itertools
import json
import math
import subprocess
import sys
from pathlib import Path

import networkx as nx
import numpy as np
import pytest
from pyproj import CRS
from shapely.geometry import LineString

import pysewer
from pysewer.bundle import NetworkBundle
from pysewer.viz import Profile, longitudinal_profile
from pysewer.viz.profile import ProfileMarker, ProfileSegment

TEST_DATA = Path("tests") / "test_data"
SINK = (691350, 2553250)

# Drop size used by the synthetic network (m). The default drop threshold
# may change; the cases use this clear drop or exactly zero.
DROP = 0.5

# Straight chain, EPSG:32633. x grows with the flow so ids are N1..N6 in order.
N1 = (500000, 5000000)  # building, start
N2 = (500100, 5000000)  # manhole (no drop)
N3 = (500200, 5000000)  # lifting station
N4 = (500400, 5000000)  # manhole with a 0.5 m drop
N5 = (500500, 5000000)  # manhole (no drop)
N6 = (500650, 5000000)  # sink (wwtp)
ID = {n: f"N{i:06d}" for i, n in enumerate([N1, N2, N3, N4, N5, N6], start=1)}


def _pairs(*points):
    return [(float(s), float(z)) for s, z in points]


def _edge(G, u, v, ground, invert, d, dod, pressurized=False):
    G.add_edge(
        u,
        v,
        geometry=LineString([u, v]),
        profile=_pairs(*ground),
        trench_depth_profile=_pairs(*invert),
        diameter=d,
        pressurized=pressurized,
        slope=0.01,
        peak_flow=0.001,
        velocity=0.8,
        d_over_D=dod,
        mean_td=1.0,
    )


def _chain():
    G = nx.DiGraph(crs=CRS("EPSG:32633"))
    G.add_node(N1, node_type="building", elevation=110.0)
    G.add_node(N2, node_type=None, elevation=108.0)
    G.add_node(
        N3, node_type=None, elevation=106.0, lifting_station=True, total_static_head=1.0
    )
    G.add_node(N4, node_type=None, elevation=108.0)
    G.add_node(N5, node_type=None, elevation=107.0)
    G.add_node(N6, node_type="wwtp", elevation=105.0)
    # P1: 100 m, gravity, D 0.2, d/D 0.5
    _edge(
        G,
        N1,
        N2,
        [(0, 110), (50, 109), (100, 108)],
        [(0, 108.5), (50, 107.5), (100, 106.5)],
        0.2,
        0.5,
    )
    # P2: 100 m, gravity, D 0.3, d/D 0.4; starts at 106.5 = end of P1 (no drop)
    _edge(G, N2, N3, [(0, 108), (100, 106)], [(0, 106.5), (100, 104.5)], 0.3, 0.4)
    # P3: 200 m pressurised, D 0.1; starts at 105.5 (jump up 1.0 from 104.5)
    _edge(
        G,
        N3,
        N4,
        [(0, 106), (100, 107), (200, 108)],
        [(0, 105.5), (200, 107.0)],
        0.1,
        1.0,
        pressurized=True,
    )
    # P4: 100 m, D 0.4, d/D 0.6; starts at 106.5, inflow invert 107.0 (drop 0.5)
    _edge(G, N4, N5, [(0, 108), (100, 107)], [(0, 106.5), (100, 105.5)], 0.4, 0.6)
    # P5: 150 m, D 0.4, d/D 0.25; starts at 105.5 = end of P4
    _edge(G, N5, N6, [(0, 107), (150, 105)], [(0, 105.5), (150, 104.0)], 0.4, 0.25)
    return G


@pytest.fixture
def G():
    return _chain()


@pytest.fixture
def b(G):
    return NetworkBundle.from_graph(G, name="chain")


@pytest.fixture
def prof(b):
    return longitudinal_profile(b, "N000001")


def _pts(points):
    return [(round(s, 9), round(z, 9)) for s, z in points]


def _assert_pts(got, want):
    assert len(got) == len(want)
    for (gs, gz), (ws, wz) in zip(got, want, strict=True):
        assert gs == pytest.approx(ws, abs=1e-9)
        assert gz == pytest.approx(wz, abs=1e-9)


# ------------------------------------------------------------------ contract
def test_ids_and_totals(prof):
    assert prof.node_ids == [f"N{i:06d}" for i in range(1, 7)]
    assert prof.pipe_ids == [f"P{i:06d}" for i in range(1, 6)]
    assert prof.start == "N000001"
    assert prof.sink == "N000006"
    # 100 + 100 + 200 + 100 + 150
    assert prof.total_length == pytest.approx(650.0, abs=1e-9)
    assert CRS.from_wkt(prof.crs_wkt) == CRS("EPSG:32633")


def test_segment_chainages_and_types(prof):
    got = [
        (s.chainage_start, s.chainage_end, s.pipe_type, s.diameter)
        for s in prof.segments
    ]
    assert got == [
        (0.0, 100.0, "gravity", 0.2),
        (100.0, 200.0, "gravity", 0.3),
        (200.0, 400.0, "pressurized", 0.1),
        (400.0, 500.0, "gravity", 0.4),
        (500.0, 650.0, "gravity", 0.4),
    ]
    assert [(s.from_node, s.to_node) for s in prof.segments] == [
        ("N000001", "N000002"),
        ("N000002", "N000003"),
        ("N000003", "N000004"),
        ("N000004", "N000005"),
        ("N000005", "N000006"),
    ]


def test_ground_global_chainage(prof):
    # P1 local s 0,50,100 -> global 0,50,100; P3 local 0,100,200 -> 200,300,400
    _assert_pts(prof.segments[0].ground, [(0, 110), (50, 109), (100, 108)])
    _assert_pts(prof.segments[2].ground, [(200, 106), (300, 107), (400, 108)])
    # P5 offset 500
    _assert_pts(prof.segments[4].ground, [(500, 107), (650, 105)])


def test_invert_global_chainage(prof):
    _assert_pts(prof.segments[0].invert, [(0, 108.5), (50, 107.5), (100, 106.5)])
    _assert_pts(prof.segments[1].invert, [(100, 106.5), (200, 104.5)])
    # pressurised: exactly two invert points
    _assert_pts(prof.segments[2].invert, [(200, 105.5), (400, 107.0)])
    _assert_pts(prof.segments[3].invert, [(400, 106.5), (500, 105.5)])
    _assert_pts(prof.segments[4].invert, [(500, 105.5), (650, 104.0)])


def test_crown_is_invert_plus_diameter(prof):
    # P1: +0.2 -> 108.7, 107.7, 106.7
    _assert_pts(prof.segments[0].crown, [(0, 108.7), (50, 107.7), (100, 106.7)])
    # P2: +0.3 -> 106.8, 104.8
    _assert_pts(prof.segments[1].crown, [(100, 106.8), (200, 104.8)])
    # P3: +0.1 -> 105.6, 107.1
    _assert_pts(prof.segments[2].crown, [(200, 105.6), (400, 107.1)])
    # P4: +0.4 -> 106.9, 105.9
    _assert_pts(prof.segments[3].crown, [(400, 106.9), (500, 105.9)])
    # P5: +0.4 -> 105.9, 104.4
    _assert_pts(prof.segments[4].crown, [(500, 105.9), (650, 104.4)])


def test_water_gravity_and_pressurised(prof):
    # P1: invert + 0.5*0.2 = +0.1 -> 108.6, 107.6, 106.6
    _assert_pts(prof.segments[0].water, [(0, 108.6), (50, 107.6), (100, 106.6)])
    # P2: +0.4*0.3 = +0.12 -> 106.62, 104.62
    _assert_pts(prof.segments[1].water, [(100, 106.62), (200, 104.62)])
    # P3 pressurised: empty
    assert prof.segments[2].water == []
    # P4: +0.6*0.4 = +0.24 -> 106.74, 105.74
    _assert_pts(prof.segments[3].water, [(400, 106.74), (500, 105.74)])
    # P5: +0.25*0.4 = +0.1 -> 105.6, 104.1
    _assert_pts(prof.segments[4].water, [(500, 105.6), (650, 104.1)])


def test_segment_hydraulics_carried(prof):
    s = prof.segments[0]
    assert (s.peak_flow, s.velocity, s.d_over_D) == (0.001, 0.8, 0.5)


def test_markers_kinds_and_inverts(prof):
    m = prof.markers
    assert [x.node_id for x in m] == prof.node_ids
    assert [x.chainage for x in m] == [0.0, 100.0, 200.0, 400.0, 500.0, 650.0]
    assert [x.kind for x in m] == [
        "start",
        "manhole",
        "lifting_station",
        "drop",
        "manhole",
        "sink",
    ]
    assert [x.ground for x in m] == [110.0, 108.0, 106.0, 108.0, 107.0, 105.0]
    # start has no incoming invert, sink no outgoing invert
    assert math.isnan(m[0].invert_in) and m[0].invert_out == 108.5
    assert (m[1].invert_in, m[1].invert_out) == (106.5, 106.5)
    # station: in 104.5, out 105.5 -> jump up 1.0
    assert (m[2].invert_in, m[2].invert_out) == (104.5, 105.5)
    assert m[2].invert_out - m[2].invert_in == pytest.approx(1.0, abs=1e-12)
    # drop: in 107.0, out 106.5 -> 0.5 m drop
    assert (m[3].invert_in, m[3].invert_out) == (107.0, 106.5)
    assert m[3].invert_in - m[3].invert_out == pytest.approx(DROP, abs=1e-12)
    assert (m[4].invert_in, m[4].invert_out) == (105.5, 105.5)
    assert m[5].invert_in == 104.0 and math.isnan(m[5].invert_out)
    assert [x.role for x in m] == [
        "building",
        "junction",
        "lifting_station",
        "junction",
        "junction",
        "sink",
    ]


def test_marker_chainage_is_cumulative_length(prof, b):
    lengths = b.pipes.set_index("pipe_id").loc[prof.pipe_ids, "length"].to_numpy()
    cum = np.concatenate([[0.0], np.cumsum(lengths)])
    assert np.allclose([m.chainage for m in prof.markers], cum, atol=1e-9)
    assert prof.total_length == pytest.approx(cum[-1], abs=1e-9)


def test_accepts_graph_directly(G, prof):
    assert longitudinal_profile(G, "N000001") == prof


def test_start_mid_path(b):
    p = longitudinal_profile(b, "N000003")
    assert p.node_ids == ["N000003", "N000004", "N000005", "N000006"]
    # 200 + 100 + 150
    assert p.total_length == pytest.approx(450.0, abs=1e-9)
    assert p.markers[0].kind == "start"  # start wins over lifting_station
    assert p.markers[0].chainage == 0.0
    _assert_pts(p.segments[0].invert, [(0, 105.5), (200, 107.0)])


def test_start_is_sink_gives_empty_profile(b):
    p = longitudinal_profile(b, "N000006")
    assert p.node_ids == ["N000006"]
    assert p.pipe_ids == [] and p.segments == []
    assert p.total_length == 0.0
    assert len(p.markers) == 1 and p.sink == "N000006"
    a = p.to_arrays()
    assert a["chainage"].size == 0 and a["segment"].size == 0


def test_explicit_sink_ok(b):
    p = longitudinal_profile(b, "N000001", sink="N000006")
    assert p.sink == "N000006"
    assert longitudinal_profile(b, "N000001", sink=ID[N6]).total_length == 650.0


def test_no_drop_when_inverts_continue():
    G = _chain()
    # raise P4 invert so it starts at 107.0 = inflow invert: no drop
    G.edges[N4, N5]["trench_depth_profile"] = _pairs((0, 107.0), (100, 105.5))
    p = longitudinal_profile(G, "N000001")
    assert p.markers[3].kind == "manhole"


def test_pumping_station_kind():
    G = _chain()
    G.nodes[N2]["pumping_station"] = True
    p = longitudinal_profile(G, "N000001")
    assert p.markers[1].kind == "pumping_station"


# ------------------------------------------------------------------ start refs
def test_tuple_snap_exact_and_within_tolerance(b):
    assert longitudinal_profile(b, N1).start == "N000001"
    # 0.0005 m away < 1e-3
    assert longitudinal_profile(b, (N3[0] + 0.0005, N3[1])).start == "N000003"
    assert longitudinal_profile(b, [N3[0], N3[1] - 0.0005]).start == "N000003"


def test_tuple_out_of_tolerance(b):
    with pytest.raises(ValueError):
        longitudinal_profile(b, (N3[0] + 0.01, N3[1]))


def test_sink_tuple_snap_and_bad_tuple(b):
    assert longitudinal_profile(b, N1, sink=N6).sink == "N000006"
    with pytest.raises(ValueError):
        longitudinal_profile(b, N1, sink=(N6[0] + 1.0, N6[1]))


# ------------------------------------------------------------------ errors
def test_unknown_node_id(b):
    with pytest.raises(ValueError):
        longitudinal_profile(b, "N999999")
    with pytest.raises(ValueError):
        longitudinal_profile(b, "N000001", sink="N999999")


def test_bad_start_type(b):
    with pytest.raises(ValueError):
        longitudinal_profile(b, 5)


def test_branching_node_raises():
    G = _chain()
    side = (500100, 5000100)
    G.add_node(side, node_type=None, elevation=107.0)
    _edge(G, N2, side, [(0, 108), (100, 107)], [(0, 106.0), (100, 105.0)], 0.2, 0.3)
    with pytest.raises(ValueError, match="outgoing"):
        longitudinal_profile(G, "N000001")
    # starting downstream of the branch still works (the side node shifts the
    # ids, so address N3 by coordinate): 200 + 100 + 150
    assert longitudinal_profile(G, N3).total_length == pytest.approx(450.0, abs=1e-9)


def test_cycle_raises():
    G = _chain()
    G.remove_edge(N5, N6)
    _edge(G, N5, N2, [(0, 107), (100, 108)], [(0, 105.0), (100, 106.0)], 0.2, 0.3)
    # N2 -> N3 -> N4 -> N5 -> N2
    with pytest.raises(ValueError, match="cycle"):
        longitudinal_profile(G, ID[N1])


def test_sink_not_reached_raises(b):
    with pytest.raises(ValueError):
        longitudinal_profile(b, "N000001", sink="N000004")
    with pytest.raises(ValueError):
        longitudinal_profile(b, "N000004", sink="N000001")  # upstream node


# ------------------------------------------------------------------ missing data
def test_missing_d_over_D_and_profiles_give_empty_water():
    G = _chain()
    del G.edges[N1, N2]["d_over_D"]
    p = longitudinal_profile(G, "N000001")
    assert p.segments[0].water == []
    assert math.isnan(p.segments[0].d_over_D)
    assert len(p.segments[1].water) == 2
    a = p.to_arrays()
    first = a["segment"] == 0
    assert np.isnan(a["water"][first]).all()
    assert not np.isnan(a["invert"][first]).any()


def test_edge_without_profile_uses_row_ends(b):
    G = _chain()
    del G.edges[N2, N3]["profile"]
    del G.edges[N2, N3]["trench_depth_profile"]
    p = longitudinal_profile(G, "N000001")
    s = p.segments[1]
    # no levels at all: empty lines, but chainage is still correct
    assert s.invert == [] and s.ground == [] and s.water == []
    assert (s.chainage_start, s.chainage_end) == (100.0, 200.0)
    assert p.total_length == pytest.approx(650.0, abs=1e-9)
    assert p.markers[2].kind == "lifting_station"
    assert math.isnan(p.markers[2].invert_in)


# ------------------------------------------------------------------ to_arrays
def test_to_arrays_layout(prof):
    a = prof.to_arrays()
    assert set(a) == {
        "chainage",
        "ground",
        "invert",
        "crown",
        "water",
        "segment",
        "pressurized",
    }
    n = a["chainage"].size
    assert all(a[k].shape == (n,) for k in a)
    assert a["pressurized"].dtype == bool
    assert np.issubdtype(a["segment"].dtype, np.integer)
    # grid sizes per segment: 3, 2, 3 (ground 0/100/200 + invert ends), 2, 2
    # P3 grid = union{200,300,400} U {200,400} = 3 points
    # rows = 3 + 2 + 3 + 2 + 2 = 12, plus 4 separators = 16
    assert n == 16
    sep = np.where(a["segment"] == -1)[0]
    assert sep.tolist() == [3, 6, 10, 13]
    for k in ("chainage", "ground", "invert", "crown", "water"):
        assert np.isnan(a[k][sep]).all()
    # exactly one separator row between pipes
    assert (np.diff(sep) > 1).all()
    assert a["segment"][0] == 0 and a["segment"][-1] == 4


def test_to_arrays_monotone_per_segment_and_overall(prof):
    a = prof.to_arrays()
    x = a["chainage"][a["segment"] >= 0]
    assert (np.diff(x) >= 0).all()
    for i in range(5):
        xs = a["chainage"][a["segment"] == i]
        assert (np.diff(xs) > 0).all()
    assert x[0] == 0.0 and x[-1] == pytest.approx(650.0, abs=1e-9)


def test_to_arrays_values(prof):
    a = prof.to_arrays()
    # segment 2 (pressurised): chainage 200, 300, 400
    m = a["segment"] == 2
    assert a["chainage"][m].tolist() == [200.0, 300.0, 400.0]
    # invert linear 105.5 -> 107.0 over 200 m: mid 106.25
    assert a["invert"][m] == pytest.approx([105.5, 106.25, 107.0], abs=1e-9)
    # crown = invert + 0.1
    assert a["crown"][m] == pytest.approx([105.6, 106.35, 107.1], abs=1e-9)
    assert a["ground"][m] == pytest.approx([106.0, 107.0, 108.0], abs=1e-9)
    assert np.isnan(a["water"][m]).all()
    assert a["pressurized"][m].all()
    assert not a["pressurized"][~m].any()
    # segment 0 gravity: water = invert + 0.1
    m0 = a["segment"] == 0
    assert a["water"][m0] == pytest.approx([108.6, 107.6, 106.6], abs=1e-9)
    # station jump: invert at end of seg 1 (104.5) and start of seg 2 (105.5)
    # are separated by a NaN row
    i_end = np.where(a["segment"] == 1)[0][-1]
    assert a["invert"][i_end] == 104.5
    assert np.isnan(a["invert"][i_end + 1])
    assert a["invert"][i_end + 2] == 105.5


# ------------------------------------------------------------------ dict / json
def test_to_dict_schema_and_json_safe(prof):
    d = prof.to_dict()
    assert d["schema"] == "profile/1"
    assert d["units"] == {
        "chainage": "m",
        "elevation": "m",
        "diameter": "m",
        "peak_flow": "m3/s",
        "velocity": "m/s",
        "d_over_D": "1",
    }
    assert d["params"] == {"drop_threshold": 0.05, "lift_tol": 1e-6}
    assert d["start"] == "N000001" and d["sink"] == "N000006"
    assert d["total_length"] == pytest.approx(650.0, abs=1e-9)
    assert d["node_ids"] == prof.node_ids and d["pipe_ids"] == prof.pipe_ids
    assert len(d["segments"]) == 5 and len(d["markers"]) == 6
    # strict JSON: no NaN literal
    text = json.dumps(d, allow_nan=False)
    assert "NaN" not in text
    assert d["markers"][0]["invert_in"] is None
    assert d["markers"][-1]["invert_out"] is None
    assert d["segments"][2]["water"] == []
    assert d["segments"][0]["water"][0] == pytest.approx([0.0, 108.6], abs=1e-9)


def test_nan_becomes_none_in_segment():
    G = _chain()
    del G.edges[N1, N2]["velocity"]
    d = longitudinal_profile(G, "N000001").to_dict()
    assert d["segments"][0]["velocity"] is None
    json.dumps(d, allow_nan=False)


def test_round_trip(prof):
    back = Profile.from_dict(json.loads(json.dumps(prof.to_dict())))
    assert back == prof
    assert back.to_dict() == prof.to_dict()
    assert back.node_ids == prof.node_ids and back.start == prof.start
    assert math.isnan(back.markers[0].invert_in)
    assert isinstance(back.segments[0], ProfileSegment)
    assert isinstance(back.markers[0], ProfileMarker)
    assert back.segments[2].water == []
    a, c = prof.to_arrays(), back.to_arrays()
    for k in a:
        np.testing.assert_array_equal(a[k], c[k])


def test_from_dict_rejects_other_schema(prof):
    d = prof.to_dict()
    d["schema"] = "profile/2"
    with pytest.raises(ValueError):
        Profile.from_dict(d)
    d.pop("schema")
    with pytest.raises(ValueError):
        Profile.from_dict(d)


# ------------------------------------------------------------------ import guard
def test_import_viz_is_light():
    code = (
        "import sys, pysewer.viz\n"
        "from pysewer.viz import longitudinal_profile\n"
        "bad = [m for m in ('matplotlib', 'osmnx') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ------------------------------------------------------------------ real data
@pytest.fixture(scope="module")
def real():
    md = pysewer.ModelDomain(
        TEST_DATA / "dem.tif",
        TEST_DATA / "roads_clipped.shp",
        TEST_DATA / "buildings_clipped.shp",
    )
    md.add_sink(SINK)
    cg = md.generate_connection_graph()
    sinks = md.get_sinks()
    G = pysewer.route_and_design(cg, sinks, station_cost=500, max_iterations=2)
    return NetworkBundle.from_graph(G, name="real", sinks=sinks)


def test_real_longest_path_leaf(real):
    pipes = real.pipes
    succ = {r.from_node: (r.to_node, float(r.length)) for r in pipes.itertuples()}
    assert len(succ) == len(pipes)  # every node has at most one outgoing pipe
    has_in = set(pipes.to_node)
    leaves = [n for n in succ if n not in has_in]
    assert leaves

    def walk(n):
        total, nodes = 0.0, [n]
        while n in succ:
            n, ln = succ[n]
            total += ln
            nodes.append(n)
        return total, nodes

    walks = {n: walk(n) for n in leaves}
    leaf = max(walks, key=lambda n: (walks[n][0], len(walks[n][1])))
    want_total, want_nodes = walks[leaf]

    p = longitudinal_profile(real, leaf)
    assert p.node_ids == want_nodes
    assert p.total_length == pytest.approx(want_total, abs=1e-6)
    assert len(p.markers) == len(want_nodes)
    assert len(p.segments) == len(want_nodes) - 1

    # chainage monotone and markers equal the cumulative length
    lens = {(r.from_node, r.to_node): float(r.length) for r in pipes.itertuples()}
    cum = 0.0
    assert p.markers[0].chainage == 0.0
    for i, (u, v) in enumerate(itertools.pairwise(want_nodes)):
        cum += lens[(u, v)]
        assert p.markers[i + 1].chainage == pytest.approx(cum, abs=1e-6)
        seg = p.segments[i]
        assert seg.chainage_end == pytest.approx(cum, abs=1e-6)
        assert seg.chainage_end > seg.chainage_start
    ch = [m.chainage for m in p.markers]
    assert all(b2 > a2 for a2, b2 in itertools.pairwise(ch))
    assert p.markers[0].kind == "start"
    assert p.markers[-1].kind == "sink"

    arr = p.to_arrays()
    good = arr["chainage"][~np.isnan(arr["chainage"])]
    assert (np.diff(good) >= -1e-9).all()
    assert good[-1] == pytest.approx(want_total, abs=1e-6)
    json.dumps(p.to_dict(), allow_nan=False)
    assert Profile.from_dict(p.to_dict()) == p


def test_real_every_leaf_reaches_a_sink(real):
    sink_ids = set(real.nodes.loc[real.nodes.role == "sink", "node_id"])
    pipes = real.pipes
    leaves = sorted(set(pipes.from_node) - set(pipes.to_node))
    for leaf in leaves[:20]:
        p = longitudinal_profile(real, leaf)
        assert p.sink in sink_ids
        assert p.total_length > 0


# ------------------------------------------------------------------ drop threshold
def _step_graph(step):
    """Chain where the invert at N4 steps down by `step` (in 107.0, out 107 - step)."""
    G = _chain()
    G.edges[N4, N5]["trench_depth_profile"] = _pairs((0, 107.0 - step), (100, 105.5))
    return G


def test_default_drop_threshold_value():
    from pysewer.viz.profile import DROP_THRESHOLD

    assert DROP_THRESHOLD == 0.05


def test_drop_threshold_boundaries():
    # inflow invert 107.0; outflow 107.0 - step
    # step 0.049 < 0.05 -> manhole; 0.05 and 0.06 -> drop
    # (outflow 106.95 is exactly representable-ish; 107.0 - 106.95 may be
    # 0.04999999999999716 in floats, so use a step just above the boundary too)
    assert (
        longitudinal_profile(_step_graph(0.049), "N000001").markers[3].kind == "manhole"
    )
    assert (
        longitudinal_profile(_step_graph(0.0), "N000001").markers[3].kind == "manhole"
    )
    assert longitudinal_profile(_step_graph(0.051), "N000001").markers[3].kind == "drop"
    assert longitudinal_profile(_step_graph(0.06), "N000001").markers[3].kind == "drop"


def test_drop_threshold_exactly_005():
    # invert_in - invert_out == 0.05 within float noise; use exactly
    # representable levels: in 107.0, out 106.9375 (step 0.0625 > 0.05) and
    # an explicit threshold equal to the step (0.0625 is a power of two).
    G = _step_graph(0.0625)
    assert longitudinal_profile(G, "N000001").markers[3].kind == "drop"
    assert (
        longitudinal_profile(G, "N000001", drop_threshold=0.0625).markers[3].kind
        == "drop"
    )
    assert (
        longitudinal_profile(G, "N000001", drop_threshold=0.0626).markers[3].kind
        == "manhole"
    )


def test_custom_drop_threshold_changes_result(b):
    # the standard chain has a 0.5 m drop at N4
    assert longitudinal_profile(b, "N000001").markers[3].kind == "drop"
    assert (
        longitudinal_profile(b, "N000001", drop_threshold=0.3).markers[3].kind == "drop"
    )
    # threshold above 0.5 -> the same node is a plain manhole
    assert (
        longitudinal_profile(b, "N000001", drop_threshold=0.6).markers[3].kind
        == "manhole"
    )
    # 0.2 m step: manhole by threshold 0.3, drop by default
    G = _step_graph(0.2)
    assert longitudinal_profile(G, "N000001").markers[3].kind == "drop"
    assert (
        longitudinal_profile(G, "N000001", drop_threshold=0.3).markers[3].kind
        == "manhole"
    )


def test_negative_step_is_not_a_drop():
    # invert goes UP by 0.2 at a manhole: never a drop
    G = _step_graph(-0.2)
    assert longitudinal_profile(G, "N000001").markers[3].kind == "manhole"


# ------------------------------------------------------------------ pump_relay
# Chain: building A -> manhole -> pumping station S (gravity in) -> pressurised
# main -> pumping station R -> pressurised main -> sink.
A1 = (600000, 5000000)
A2 = (600100, 5000000)
A3 = (600200, 5000000)  # pumping station S (gravity in, lift)
A4 = (600400, 5000000)  # pumping station R (pressurised in, same invert out)
A5 = (600600, 5000000)  # sink


def _relay(out_offset=0.0):
    G = nx.DiGraph(crs=CRS("EPSG:32633"))
    G.add_node(A1, node_type="building", elevation=110.0)
    G.add_node(A2, node_type=None, elevation=108.0)
    G.add_node(A3, node_type=None, elevation=106.0, pumping_station=True)
    G.add_node(A4, node_type=None, elevation=107.0, pumping_station=True)
    G.add_node(A5, node_type="wwtp", elevation=105.0)
    _edge(G, A1, A2, [(0, 110), (100, 108)], [(0, 108.5), (100, 106.5)], 0.2, 0.5)
    _edge(G, A2, A3, [(0, 108), (100, 106)], [(0, 106.5), (100, 104.5)], 0.3, 0.4)
    # P3 pressurised: starts 105.5 (jump up 1.0 at S), ends 107.0
    _edge(
        G,
        A3,
        A4,
        [(0, 106), (200, 107)],
        [(0, 105.5), (200, 107.0)],
        0.1,
        1.0,
        pressurized=True,
    )
    # P4 pressurised: starts at 107.0 + out_offset
    _edge(
        G,
        A4,
        A5,
        [(0, 107), (200, 105)],
        [(0, 107.0 + out_offset), (200, 106.0)],
        0.1,
        1.0,
        pressurized=True,
    )
    return G


def test_pump_relay_marker():
    p = longitudinal_profile(_relay(), "N000001")
    assert p.node_ids == [f"N{i:06d}" for i in range(1, 6)]
    # chainage 0, 100, 200, 400, 600 (100 + 100 + 200 + 200)
    assert [m.chainage for m in p.markers] == [0.0, 100.0, 200.0, 400.0, 600.0]
    assert [m.kind for m in p.markers] == [
        "start",
        "manhole",
        "pumping_station",
        "pump_relay",
        "sink",
    ]
    # S: in 104.5, out 105.5; R: in 107.0, out 107.0 (no lift)
    m = p.markers
    assert (m[2].invert_in, m[2].invert_out) == (104.5, 105.5)
    assert (m[3].invert_in, m[3].invert_out) == (107.0, 107.0)


def test_pump_relay_tolerance_boundaries():
    # |out - in| = 1e-7 <= 1e-6 -> relay
    assert longitudinal_profile(_relay(1e-7), "N000001").markers[3].kind == "pump_relay"
    # below the invert too
    assert (
        longitudinal_profile(_relay(-1e-7), "N000001").markers[3].kind == "pump_relay"
    )
    # |out - in| = 1e-3 > 1e-6 -> plain pumping station
    assert (
        longitudinal_profile(_relay(1e-3), "N000001").markers[3].kind
        == "pumping_station"
    )
    assert (
        longitudinal_profile(_relay(0.5), "N000001").markers[3].kind
        == "pumping_station"
    )


def test_pumping_station_after_gravity_pipe_is_not_relay():
    # a pumping station with a gravity inflow and same invert is not a relay
    G = _relay()
    G.edges[A2, A3]["pressurized"] = False
    G.edges[A3, A4]["trench_depth_profile"] = _pairs((0, 104.5), (200, 107.0))
    p = longitudinal_profile(G, "N000001")
    assert p.markers[2].kind == "pumping_station"


def test_pump_relay_requires_pumping_not_lifting():
    G = _relay()
    G.nodes[A4]["pumping_station"] = False
    G.nodes[A4]["lifting_station"] = True
    assert longitudinal_profile(G, "N000001").markers[3].kind == "lifting_station"


def test_pump_relay_as_start_stays_start():
    p = longitudinal_profile(_relay(), A4)
    assert p.markers[0].kind == "start"


# ------------------------------------------------------------------ real data nodes
def test_real_pump_relay_and_pumping_station(real):
    nodes = real.nodes.set_index("node_id")
    if "N000018" not in nodes.index or "N000115" not in nodes.index:
        pytest.skip("node ids changed")
    p = longitudinal_profile(real, "N000115")
    assert "N000018" in p.node_ids
    kinds = {m.node_id: m.kind for m in p.markers}
    assert kinds["N000018"] == "pump_relay"
    m = p.markers[p.node_ids.index("N000018")]
    assert abs(m.invert_out - m.invert_in) <= 1e-6
    # the same station is a genuine pumping station on the N000023 path
    q = longitudinal_profile(real, "N000023")
    assert "N000018" in q.node_ids
    kq = {m.node_id: m.kind for m in q.markers}
    assert kq["N000018"] == "pumping_station"
    mq = q.markers[q.node_ids.index("N000018")]
    assert abs(mq.invert_out - mq.invert_in) > 1e-6


def test_real_profile_n000115_total_length(real):
    pipes = real.pipes
    succ = {r.from_node: (r.to_node, float(r.length)) for r in pipes.itertuples()}
    n, total, ids = "N000115", 0.0, ["N000115"]
    cums = [0.0]
    while n in succ:
        n, ln = succ[n]
        total += ln
        ids.append(n)
        cums.append(total)
    p = longitudinal_profile(real, "N000115")
    assert p.node_ids == ids
    assert p.total_length == pytest.approx(total, abs=1e-6)
    assert [m.chainage for m in p.markers] == pytest.approx(cums, abs=1e-6)
    ch = np.array([m.chainage for m in p.markers])
    assert (np.diff(ch) > 0).all()


# ------------------------------------------------------------------ params, wgs84
def test_params_reflect_custom_drop_threshold(b):
    p = longitudinal_profile(b, "N000001", drop_threshold=0.3)
    assert p.params == {"drop_threshold": 0.3, "lift_tol": 1e-6}
    d = json.loads(json.dumps(p.to_dict(), allow_nan=False))
    assert d["params"] == {"drop_threshold": 0.3, "lift_tol": 1e-6}
    assert Profile.from_dict(d).params == {"drop_threshold": 0.3, "lift_tol": 1e-6}
    assert Profile.from_dict(d) == p


def test_profile_from_wgs84_bundle_equals_projected(b):
    w = b.to_wgs84()
    pw = longitudinal_profile(w, "N000001")
    pp = longitudinal_profile(b, "N000001")
    assert CRS.from_wkt(pw.crs_wkt) == CRS("EPSG:32633")
    assert pw.crs_wkt == pp.crs_wkt
    assert pw.total_length == pytest.approx(650.0, abs=1e-9)
    assert pw.node_ids == pp.node_ids
    assert pw.to_dict() == pp.to_dict()
