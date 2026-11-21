# SPDX-FileCopyrightText: 2025 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""compare(): variants side by side (contract compare/1)."""

import copy
import dataclasses
import json
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import pytest
from pyproj import CRS
from shapely.geometry import LineString

import pysewer
from pysewer.bundle import NetworkBundle
from pysewer.compare import Comparison, compare
from pysewer.config.manager import reset_config, set_config

TEST_DATA = Path("tests") / "test_data"
SINK = (691350, 2553250)

A = (500000.0, 5000000.0)
B = (500100.0, 5000000.0)
C = (500100.0, 5000100.0)
K = (500000.0, 5000100.0)
S = (500200.0, 5000100.0)
E = (500200.0, 5000000.0)

# base: four pipes of 100 m each (total 400 m)
BASE_EDGES = [
    (A, B, 0.2, False),
    (B, C, 0.3, False),
    (K, C, 0.2, False),
    (C, S, 0.4, False),
]


def build(edges, node_attrs=None, name="v"):
    """Bundle from (u, v, diameter, pressurized) tuples; S is the wwtp."""
    G = nx.DiGraph(crs=CRS("EPSG:32633"))
    node_attrs = node_attrs or {}
    for u, v, d, pres in edges:
        for n in (u, v):
            if n not in G:
                G.add_node(
                    n,
                    node_type="wwtp" if n == S else "building",
                    elevation=10.0,
                    **node_attrs.get(n, {}),
                )
        length = float(LineString([u, v]).length)
        G.add_edge(
            u,
            v,
            geometry=LineString([u, v]),
            diameter=d,
            pressurized=pres,
            profile=[(0.0, 10.0), (length, 9.0)],
            trench_depth_profile=[(0.0, 8.5), (length, 7.5)],
        )
    return NetworkBundle.from_graph(G, name=name)


def edit(edges, idx, **kw):
    out = list(edges)
    u, v, d, p = out[idx]
    out[idx] = (kw.get("u", u), kw.get("v", v), kw.get("d", d), kw.get("p", p))
    return out


@pytest.fixture(scope="module")
def base():
    return build(BASE_EDGES, name="base")


def cmp2(base, var, **kw):
    c = compare({"base": base, "var": var}, **kw)
    return c, c.spatial.loc["var"], c.pipe_diff["var"]


def check_identities(base, var, sp):
    b_total = base.pipes["length"].sum()
    v_total = var.pipes["length"].sum()
    assert sp["shared_length_m"] + sp["removed_length_m"] == pytest.approx(
        b_total, abs=1e-9
    )
    assert sp["shared_length_variant_m"] + sp["added_length_m"] == pytest.approx(
        v_total, abs=1e-9
    )


# ---------------------------------------------------------------- spatial


def test_identical_copy(base):
    var = dataclasses.replace(base, pipes=base.pipes.copy())
    c, sp, diff = cmp2(base, var)
    assert isinstance(c, Comparison)
    assert c.base == "base" and c.names == ("base", "var")
    assert c.tolerance == 0.01
    # 4 pipes x 100 m
    assert sp["shared_length_m"] == pytest.approx(400.0, abs=1e-9)
    assert sp["shared_length_variant_m"] == pytest.approx(400.0, abs=1e-9)
    assert sp["added_length_m"] == 0 and sp["removed_length_m"] == 0
    assert sp["changed_diameter_length_m"] == 0 and sp["changed_type_length_m"] == 0
    assert (sp["n_shared"], sp["n_added"], sp["n_removed"], sp["n_changed"]) == (
        4,
        0,
        0,
        0,
    )
    assert set(diff["status"]) == {"shared"}
    assert not diff["flow_reversed"].any()
    assert (c.metrics_delta["var"].dropna() == 0).all()
    assert c.config_diff.empty


def test_base_row_is_all_shared(base):
    c, _, _ = cmp2(base, build(BASE_EDGES[:3]))
    row = c.spatial.loc["base"]
    assert row["shared_length_m"] == pytest.approx(400.0, abs=1e-9)
    assert row["shared_length_variant_m"] == pytest.approx(400.0, abs=1e-9)
    assert row["n_shared"] == 4
    for k in (
        "added_length_m",
        "removed_length_m",
        "changed_diameter_length_m",
        "changed_type_length_m",
        "n_added",
        "n_removed",
        "n_changed",
    ):
        assert row[k] == 0
    assert "base" not in c.pipe_diff


def test_one_pipe_removed(base):
    var = build(BASE_EDGES[:3])  # K->C is index 2; drop the last, C->S, instead
    var = build([BASE_EDGES[i] for i in (0, 1, 3)])  # drop K->C (100 m)
    c, sp, diff = cmp2(base, var)
    # shared 3 x 100 = 300, removed 1 x 100
    assert sp["shared_length_m"] == pytest.approx(300.0, abs=1e-9)
    assert sp["removed_length_m"] == pytest.approx(100.0, abs=1e-9)
    assert sp["added_length_m"] == 0
    assert (sp["n_shared"], sp["n_added"], sp["n_removed"], sp["n_changed"]) == (
        3,
        0,
        1,
        0,
    )
    check_identities(base, var, sp)
    rem = diff[diff.status == "removed"]
    assert len(rem) == 1
    r = rem.iloc[0]
    # removed pipe keeps the base geometry (K -> C)
    assert r.geometry.equals(LineString([K, C]))
    assert r.length_base == pytest.approx(100.0, abs=1e-9)
    assert np.isnan(r.length_variant) and pd.isna(r.pipe_id_variant)
    assert r.diameter_base == pytest.approx(0.2, abs=1e-12)
    assert np.isnan(r.diameter_variant)
    assert r.pipe_type_base == "gravity" and pd.isna(r.pipe_type_variant)
    # metrics delta: total length -100, n_pipes -1
    assert c.metrics_delta.loc["total_length_m", "var"] == pytest.approx(
        -100.0, abs=1e-9
    )
    assert c.metrics_delta.loc["n_pipes", "var"] == -1
    assert c.metrics_delta.loc["total_length_m", "base"] == 0


def test_one_pipe_added(base):
    var = build([*BASE_EDGES, (E, S, 0.2, False)])  # E -> S is 100 m
    c, sp, diff = cmp2(base, var)
    assert sp["shared_length_m"] == pytest.approx(400.0, abs=1e-9)
    assert sp["added_length_m"] == pytest.approx(100.0, abs=1e-9)
    assert sp["removed_length_m"] == 0
    assert (sp["n_shared"], sp["n_added"], sp["n_removed"], sp["n_changed"]) == (
        4,
        1,
        0,
        0,
    )
    check_identities(base, var, sp)
    add = diff[diff.status == "added"].iloc[0]
    assert add.geometry.equals(LineString([E, S]))
    assert np.isnan(add.length_base) and pd.isna(add.pipe_id_base)
    assert add.length_variant == pytest.approx(100.0, abs=1e-9)
    assert add.pipe_type_variant == "gravity"
    assert c.metrics_delta.loc["total_length_m", "var"] == pytest.approx(
        100.0, abs=1e-9
    )
    assert c.metrics_delta.loc["n_pipes", "var"] == 1


def test_diameter_changed(base):
    var = build(edit(BASE_EDGES, 1, d=0.5))  # B->C 0.3 -> 0.5
    c, sp, diff = cmp2(base, var)
    assert sp["changed_diameter_length_m"] == pytest.approx(100.0, abs=1e-9)
    assert sp["changed_type_length_m"] == 0
    assert (sp["n_shared"], sp["n_changed"], sp["n_added"], sp["n_removed"]) == (
        4,
        1,
        0,
        0,
    )
    assert sp["shared_length_m"] == pytest.approx(
        400.0, abs=1e-9
    )  # changed counts as shared
    ch = diff[diff.status == "changed"]
    assert len(ch) == 1
    r = ch.iloc[0]
    assert r.diameter_base == pytest.approx(0.3, abs=1e-12)
    assert r.diameter_variant == pytest.approx(0.5, abs=1e-12)
    assert not r.flow_reversed
    # diameter table: base 0.3 m has 100 m, variant 0.5 m has 100 m
    assert c.diameters.loc[0.3, "base"] == pytest.approx(100.0, abs=1e-9)
    assert c.diameters.loc[0.3, "var"] == 0
    assert c.diameters.loc[0.5, "var"] == pytest.approx(100.0, abs=1e-9)
    # 0.2 m: A->B and K->C = 200 m, 2 pipes
    assert c.diameters.loc[0.2, "base"] == pytest.approx(200.0, abs=1e-9)
    assert c.diameter_counts.loc[0.2, "var"] == 2
    assert c.diameter_counts.loc[0.5, "base"] == 0


def test_diameter_within_atol_is_shared(base):
    var = build(edit(BASE_EDGES, 1, d=0.3 + 5e-7))  # below the 1e-6 atol
    _, sp, diff = cmp2(base, var)
    assert sp["n_changed"] == 0 and set(diff.status) == {"shared"}


def test_gravity_to_pressurized(base):
    var = build(edit(BASE_EDGES, 3, p=True))  # C->S pressurized
    c, sp, diff = cmp2(base, var)
    assert sp["changed_type_length_m"] == pytest.approx(100.0, abs=1e-9)
    assert sp["changed_diameter_length_m"] == 0
    assert sp["n_changed"] == 1
    r = diff[diff.status == "changed"].iloc[0]
    assert (r.pipe_type_base, r.pipe_type_variant) == ("gravity", "pressurized")
    assert c.metrics_delta.loc["pressurized_length_m", "var"] == pytest.approx(
        100.0, abs=1e-9
    )
    assert c.metrics_delta.loc["gravity_length_m", "var"] == pytest.approx(
        -100.0, abs=1e-9
    )


def test_pressurized_to_gravity():
    b = build(edit(BASE_EDGES, 3, p=True), name="b")
    v = build(BASE_EDGES, name="v")
    _, sp, diff = cmp2(b, v)
    r = diff[diff.status == "changed"].iloc[0]
    assert (r.pipe_type_base, r.pipe_type_variant) == ("pressurized", "gravity")
    assert sp["changed_type_length_m"] == pytest.approx(100.0, abs=1e-9)


def test_reversed_pipe(base):
    u, v, _d, _p = BASE_EDGES[0]
    var = build(edit(BASE_EDGES, 0, u=v, v=u))  # B->A instead of A->B
    _c, sp, diff = cmp2(base, var)
    assert sp["n_changed"] == 1 and sp["n_added"] == 0 and sp["n_removed"] == 0
    assert sp["shared_length_m"] == pytest.approx(400.0, abs=1e-9)
    assert sp["changed_diameter_length_m"] == 0 and sp["changed_type_length_m"] == 0
    r = diff[diff.status == "changed"].iloc[0]
    assert bool(r.flow_reversed) is True
    assert diff.flow_reversed.sum() == 1


def test_endpoint_moved_below_tolerance_is_shared(base):
    A2 = (500000.004, 5000000.0)  # 4 mm < 1 cm grid: rounds to the same cell
    var = build(edit(BASE_EDGES, 0, u=A2))
    _, sp, diff = cmp2(base, var)
    assert set(diff.status) == {"shared"}
    assert sp["n_shared"] == 4 and sp["n_added"] == 0 and sp["n_removed"] == 0
    # base length 100, variant length 100 - 0.004 = 99.996
    assert sp["shared_length_m"] == pytest.approx(400.0, abs=1e-9)
    assert sp["shared_length_variant_m"] == pytest.approx(399.996, abs=1e-6)
    check_identities(base, var, sp)


def test_endpoint_moved_above_tolerance_is_removed_and_added(base):
    A2 = (500005.0, 5000000.0)  # 5 m
    var = build(edit(BASE_EDGES, 0, u=A2))
    _, sp, _diff = cmp2(base, var)
    # removed: base A->B (100 m); added: A2->B (95 m)
    assert sp["removed_length_m"] == pytest.approx(100.0, abs=1e-9)
    assert sp["added_length_m"] == pytest.approx(95.0, abs=1e-9)
    assert sp["shared_length_m"] == pytest.approx(300.0, abs=1e-9)
    assert sp["shared_length_variant_m"] == pytest.approx(300.0, abs=1e-9)
    assert (sp["n_shared"], sp["n_added"], sp["n_removed"]) == (3, 1, 1)
    check_identities(base, var, sp)


def test_larger_tolerance_matches_moved_endpoint(base):
    A2 = (500005.0, 5000000.0)
    var = build(edit(BASE_EDGES, 0, u=A2))
    _, sp, _ = cmp2(
        base, var, tolerance=20.0
    )  # 5 m is within a 20 m grid cell? use rounding
    # grid cell 20 m: 500000/20 = 25000.0, 500005/20 = 25000.25 -> same cell
    assert sp["n_added"] == 0 and sp["n_removed"] == 0 and sp["n_shared"] == 4


def test_multiple_variants_and_base_choice(base):
    v1 = build([BASE_EDGES[i] for i in (0, 1, 3)])
    v2 = build([*BASE_EDGES, (E, S, 0.2, False)])
    c = compare({"a": base, "b": v1, "c": v2}, base="b")
    assert c.base == "b" and set(c.pipe_diff) == {"a", "c"}
    assert list(c.spatial.index) == ["a", "b", "c"]
    # against b (3 pipes, 300 m): a adds K->C (100 m), c adds K->C and E->S
    assert c.spatial.loc["a", "added_length_m"] == pytest.approx(100.0, abs=1e-9)
    assert c.spatial.loc["c", "added_length_m"] == pytest.approx(200.0, abs=1e-9)
    assert c.spatial.loc["b", "shared_length_m"] == pytest.approx(300.0, abs=1e-9)
    assert (c.metrics_delta["b"].dropna() == 0).all()
    # default base is the first key
    assert compare({"a": base, "b": v1}).base == "a"


def test_single_bundle_compare(base):
    c = compare({"only": base})
    assert c.names == ("only",) and c.base == "only"
    assert c.pipe_diff == {}
    assert c.spatial.loc["only", "shared_length_m"] == pytest.approx(400.0, abs=1e-9)
    assert c.config_diff.empty
    assert list(c.metrics.columns) == ["only"]
    assert c.metrics.loc["n_pipes", "only"] == 4
    d = c.to_dict()
    json.dumps(d, allow_nan=False)
    assert d["pipe_diff"] == {}


def test_graph_input_converted(base):
    G = nx.DiGraph(crs=CRS("EPSG:32633"))
    for u, v, d, p in BASE_EDGES:
        G.add_node(u, node_type="wwtp" if u == S else "building", elevation=10.0)
        G.add_node(v, node_type="wwtp" if v == S else "building", elevation=10.0)
        G.add_edge(u, v, geometry=LineString([u, v]), diameter=d, pressurized=p)
    c = compare({"base": base, "g": G})
    assert c.spatial.loc["g", "n_shared"] == 4
    assert c.spatial.loc["g", "removed_length_m"] == 0


# ---------------------------------------------------------------- config


def with_config(b, mutate):
    meta = copy.deepcopy(b.metadata)
    mutate(meta["config"])
    return dataclasses.replace(b, metadata=meta)


def test_config_diff_only_differing_keys(base):
    v1 = with_config(base, lambda c: c["optimization"].__setitem__("tmax", 3))
    c, _, _ = cmp2(base, v1)
    cd = c.config_diff
    assert list(cd.index) == ["optimization.tmax"]
    assert list(cd.columns) == ["base", "var"]
    assert cd.loc["optimization.tmax", "var"] == 3
    assert (
        cd.loc["optimization.tmax", "base"]
        == base.metadata["config"]["optimization"]["tmax"]
    )


def test_config_diff_list_and_missing_key(base):
    def mut(cfg):
        cfg["optimization"]["extra_list"] = [1, 2]
        del cfg["optimization"]["tmin"]

    v1 = with_config(base, mut)
    c, _, _ = cmp2(base, v1)
    cd = c.config_diff
    assert set(cd.index) == {"optimization.extra_list", "optimization.tmin"}
    assert cd.loc["optimization.extra_list", "var"] == "[1, 2]"  # JSON string
    assert pd.isna(
        cd.loc["optimization.extra_list", "base"]
    )  # missing key (pandas stores None as NaN)
    assert pd.isna(cd.loc["optimization.tmin", "var"])
    assert (
        cd.loc["optimization.tmin", "base"]
        == base.metadata["config"]["optimization"]["tmin"]
    )


def test_config_diff_three_variants_two_equal(base):
    v1 = with_config(base, lambda c: c["optimization"].__setitem__("tmax", 3))
    v2 = with_config(base, lambda c: c["optimization"].__setitem__("tmax", 3))
    c = compare({"a": base, "b": v1, "c": v2})
    assert list(c.config_diff.index) == ["optimization.tmax"]
    assert (
        c.config_diff.loc["optimization.tmax", "b"]
        == c.config_diff.loc["optimization.tmax", "c"]
    )


# ---------------------------------------------------------------- stations / metrics


def test_stations():
    b = build(BASE_EDGES, name="b")
    attrs = {
        C: {
            "pumping_station": True,
            "total_static_head": 1.0,
        },  # junction? C is "building"
    }
    # C is typed as a building by build(): that makes it a private pump.
    v = build(BASE_EDGES, node_attrs=attrs, name="v")
    c = compare({"b": b, "v": v})
    assert c.stations.index.tolist() == [
        "pumping_stations",
        "lifting_stations",
        "private_pumps",
    ]
    assert c.stations["b"].tolist() == [0, 0, 0]
    assert c.stations["v"].tolist() == [0, 0, 1]


def test_stations_pumping_and_lifting():
    # B and C are junctions (node_type None) with pumping/lifting flags
    G = nx.DiGraph(crs=CRS("EPSG:32633"))
    G.add_node(A, node_type="building", elevation=10.0)
    G.add_node(B, node_type=None, elevation=10.0, lifting_station=True)
    G.add_node(C, node_type=None, elevation=10.0, pumping_station=True)
    G.add_node(S, node_type="wwtp", elevation=10.0)
    for u, v in [(A, B), (B, C), (C, S)]:
        G.add_edge(u, v, geometry=LineString([u, v]), diameter=0.2, pressurized=False)
    v = NetworkBundle.from_graph(G, name="v")
    c = compare({"b": build(BASE_EDGES), "v": v})
    assert c.stations["v"].tolist() == [1, 1, 0]
    assert c.stations["b"].tolist() == [0, 0, 0]
    assert c.metrics_delta.loc["n_pumping_stations", "v"] == 1
    assert c.metrics_delta.loc["n_lifting_stations", "v"] == 1


def test_metrics_delta_nan_when_undefined(base):
    # all pipes pressurized -> no gravity pipes -> mean_depth_m undefined (NaN)
    var = build([(u, v, d, True) for u, v, d, _ in BASE_EDGES])
    c, _, _ = cmp2(base, var)
    assert not np.isnan(c.metrics.loc["mean_depth_m", "base"])
    assert np.isnan(c.metrics.loc["mean_depth_m", "var"])
    assert np.isnan(c.metrics_delta.loc["mean_depth_m", "var"])
    assert c.metrics_delta.loc["pressurized_length_m", "var"] == pytest.approx(
        400.0, abs=1e-9
    )
    assert c.metrics_delta.loc["gravity_length_m", "var"] == pytest.approx(
        -400.0, abs=1e-9
    )


def test_metrics_exclude_non_scalars(base):
    c, _, _ = cmp2(base, base)
    for k in ("diameter_count", "diameter_length_m", "schema", "hydraulic_violations"):
        assert k not in c.metrics.index
    assert (c.metrics.dtypes == "float64").all()


# ---------------------------------------------------------------- to_dict


def test_to_dict_json_safe_and_lonlat(base):
    var = build([BASE_EDGES[0], BASE_EDGES[1], BASE_EDGES[3], (E, S, 0.2, False)])
    c, _, _ = cmp2(base, var)
    c = dataclasses.replace(
        c, metrics_delta=c.metrics_delta.assign(nanner=np.nan)
    )  # force a NaN into a table
    d = c.to_dict()
    text = json.dumps(d, allow_nan=False)
    d = json.loads(text)
    assert d["schema"] == "compare/1"
    assert d["base"] == "base" and d["names"] == ["base", "var"]
    assert d["tolerance"] == 0.01
    for key in (
        "metrics",
        "metrics_delta",
        "config_diff",
        "diameters",
        "diameter_counts",
        "stations",
        "spatial",
    ):
        assert set(d[key]) == {"index", "columns", "data"}
    assert (
        d["metrics_delta"]["data"][-1] == [None, None]
        or None in d["metrics_delta"]["data"][-1]
    )
    fc = d["pipe_diff"]["var"]
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 5  # 3 shared + 1 removed + 1 added
    assert {f["properties"]["status"] for f in fc["features"]} == {
        "shared",
        "removed",
        "added",
    }
    for f in fc["features"]:
        lon, lat = f["geometry"]["coordinates"][0]
        # UTM 33N near the central meridian (15 E), y = 5.0e6 m -> about 45.1 N
        assert lon == pytest.approx(15.0, abs=0.01)
        assert lat == pytest.approx(45.1, abs=0.1)
    # the removed pipe K -> C starts at K
    rem = next(f for f in fc["features"] if f["properties"]["status"] == "removed")
    lon0, lat0 = rem["geometry"]["coordinates"][0]
    k_wgs = gpd.GeoSeries.from_xy([K[0]], [K[1]], crs=32633).to_crs(4326).iloc[0]
    assert lon0 == pytest.approx(k_wgs.x, abs=1e-9)
    assert lat0 == pytest.approx(k_wgs.y, abs=1e-9)
    # NaN length of the removed pipe's variant side is null
    assert rem["properties"]["length_variant"] is None


# ---------------------------------------------------------------- errors


def test_empty_mapping_raises():
    with pytest.raises(ValueError):
        compare({})


def test_unknown_base_raises(base):
    with pytest.raises(ValueError, match="base"):
        compare({"a": base}, base="zzz")


@pytest.mark.parametrize("tol", [0, -0.1])
def test_bad_tolerance_raises(base, tol):
    with pytest.raises(ValueError, match="tolerance"):
        compare({"a": base}, tolerance=tol)


def test_crs_mismatch_raises(base):
    other = dataclasses.replace(base, pipes=base.pipes.to_crs(32632))
    with pytest.raises(ValueError, match="CRS"):
        compare({"a": base, "b": other})


# ---------------------------------------------------------------- real data


def test_real_default_vs_tmax3():
    def design(settings):
        reset_config()
        if settings:
            set_config(custom_settings_dict=settings)
        md = pysewer.ModelDomain(
            TEST_DATA / "dem.tif",
            TEST_DATA / "roads_clipped.shp",
            TEST_DATA / "buildings_clipped.shp",
        )
        md.add_sink(SINK)
        sinks = md.get_sinks()
        G = pysewer.route_and_design(
            md.generate_connection_graph(), sinks, station_cost=500, max_iterations=2
        )
        return NetworkBundle.from_graph(G, sinks=sinks)

    try:
        b0 = design(None)
        b3 = design({"optimization": {"tmax": 3}})
    finally:
        reset_config()

    c = compare({"default": b0, "tmax3": b3})
    assert list(c.config_diff.index) == ["optimization.tmax"]
    assert c.config_diff.loc["optimization.tmax", "tmax3"] == 3
    st = c.stations
    assert (
        st.loc["pumping_stations", "tmax3"] + st.loc["lifting_stations", "tmax3"]
        > st.loc["pumping_stations", "default"] + st.loc["lifting_stations", "default"]
    )
    assert (
        c.metrics.loc["max_depth_m", "tmax3"] < c.metrics.loc["max_depth_m", "default"]
    )
    assert c.metrics_delta.loc["max_depth_m", "tmax3"] < 0
    sp = c.spatial.loc["tmax3"]
    assert sp["n_added"] == 0 and sp["n_removed"] == 0
    assert sp["n_shared"] == len(b0.pipes) == len(b3.pipes)
    check_identities(b0, b3, sp)
    json.dumps(c.to_dict(), allow_nan=False)


# ------------------------------------------- matching: tolerance semantics


def shifted(edges, dx, dy):
    return [
        ((u[0] + dx, u[1] + dy), (v[0] + dx, v[1] + dy), d, p) for u, v, d, p in edges
    ]


def test_uniform_shift_inside_and_outside_tolerance(base):
    var = build(shifted(BASE_EDGES, 0.004, 0.004))
    # both endpoints move by hypot(0.004, 0.004) = 0.00566 m
    _, sp, diff = cmp2(base, var, tolerance=0.01)  # 0.00566 <= 0.01
    assert set(diff.status) == {"shared"} and sp["n_shared"] == 4
    assert sp["n_added"] == 0 and sp["n_removed"] == 0
    _, sp, _ = cmp2(base, var, tolerance=0.005)  # 0.00566 > 0.005
    assert (sp["n_shared"], sp["n_added"], sp["n_removed"]) == (0, 4, 4)
    assert sp["removed_length_m"] == pytest.approx(400.0, abs=1e-9)
    assert sp["added_length_m"] == pytest.approx(400.0, abs=1e-6)


def test_cell_boundary_straddle():
    # cell size 0.01: x = 0.0099 is in cell 0, x = 0.0101 in cell 1; distance 0.0002
    b = build([((0.0099, 0.0), (100.0, 0.0), 0.2, False)], name="b")
    v = build([((0.0101, 0.0), (100.0, 0.0), 0.2, False)])
    _, sp, _ = cmp2(b, v)
    assert (sp["n_shared"], sp["n_added"], sp["n_removed"]) == (1, 0, 0)
    # same with the far end straddling a boundary, and in y
    b = build([((0.0, 0.0), (0.0, 99.9999), 0.2, False)], name="b")
    v = build([((0.0, 0.0), (0.0, 100.0001), 0.2, False)])
    _, sp, _ = cmp2(b, v)
    assert (sp["n_shared"], sp["n_added"], sp["n_removed"]) == (1, 0, 0)
    # negative coordinates around zero (cell -1 and cell 0)
    b = build([((-0.0001, 0.0), (100.0, 0.0), 0.2, False)], name="b")
    v = build([((0.0001, 0.0), (100.0, 0.0), 0.2, False)])
    _, sp, _ = cmp2(b, v)
    assert (sp["n_shared"], sp["n_added"], sp["n_removed"]) == (1, 0, 0)


def test_single_endpoint_shift_at_tolerance_edge():
    tol = 0.01
    b = build([((0.0, 0.0), (100.0, 0.0), 0.2, False)], name="b")
    # one end moves by tol * 0.999 = 0.00999, the other stays
    v = build([((0.00999, 0.0), (100.0, 0.0), 0.2, False)])
    _, sp, _ = cmp2(b, v, tolerance=tol)
    assert (sp["n_shared"], sp["n_added"], sp["n_removed"]) == (1, 0, 0)
    # tol * 1.001 = 0.01001
    v = build([((0.01001, 0.0), (100.0, 0.0), 0.2, False)])
    _, sp, _ = cmp2(b, v, tolerance=tol)
    assert (sp["n_shared"], sp["n_added"], sp["n_removed"]) == (0, 1, 1)


def test_reversed_with_small_shift_is_changed(base):
    u, v, _d, _p = BASE_EDGES[0]
    v2 = (v[0] + 0.003, v[1])  # 3 mm < 1 cm
    var = build(edit(BASE_EDGES, 0, u=v2, v=u))  # B' -> A
    _, sp, diff = cmp2(base, var)
    assert (sp["n_shared"], sp["n_added"], sp["n_removed"]) == (4, 0, 0)
    assert sp["n_changed"] == 1
    r = diff[diff.status == "changed"].iloc[0]
    assert bool(r.flow_reversed) is True
    assert diff.flow_reversed.sum() == 1


# ------------------------------------- matching: ties and one-to-one


PARALLEL = [(A, B, 0.2, False), (B, A, 0.3, False)]  # same end points, 2 diameters


def test_parallel_pipes_prefer_equal_diameter():
    b = build(PARALLEL, name="b")
    v = build(list(reversed(PARALLEL)))  # rows in reverse order
    _, sp, diff = cmp2(b, v)
    assert (sp["n_shared"], sp["n_added"], sp["n_removed"]) == (2, 0, 0)
    # equal diameters paired: no diameter change, and each keeps its direction
    assert sp["changed_diameter_length_m"] == 0
    assert sp["n_changed"] == 0
    assert not diff["flow_reversed"].any()
    assert set(diff["status"]) == {"shared"}


def test_parallel_pipes_order_independent():
    b = build(PARALLEL, name="b")
    v = build(list(reversed(PARALLEL)))
    _c0, sp0, diff0 = cmp2(b, v)
    cols = ["status", "flow_reversed"]
    for seed in (0, 1, 2):
        shuf = dataclasses.replace(
            v, pipes=v.pipes.sample(frac=1, random_state=seed).reset_index(drop=True)
        )
        _, sp, diff = cmp2(b, shuf)
        pd.testing.assert_series_equal(sp0, sp)
        assert sorted(map(tuple, diff[cols].values.tolist())) == sorted(
            map(tuple, diff0[cols].values.tolist())
        )
    # base shuffled too
    bs = dataclasses.replace(
        b, pipes=b.pipes.sample(frac=1, random_state=5).reset_index(drop=True)
    )
    _, sp, _ = cmp2(bs, v)
    pd.testing.assert_series_equal(sp0, sp)


def test_one_to_one_two_variants_near_one_base():
    b = build([((0.0, 0.0), (100.0, 0.0), 0.2, False)], name="b")
    v = build(
        [
            ((0.001, 0.0), (100.0, 0.0), 0.2, False),  # 1 mm off
            ((0.0, 0.002), (100.0, 0.0), 0.2, False),  # 2 mm off
        ]
    )
    _, sp, _diff = cmp2(b, v)
    assert (sp["n_shared"], sp["n_added"], sp["n_removed"]) == (1, 1, 0)
    # the closer variant (1 mm) wins: variant length 99.999 shared,
    # the other (sqrt(100^2 + 0.002^2) = 100.00000002) is added
    assert sp["shared_length_m"] == pytest.approx(100.0, abs=1e-9)
    assert sp["shared_length_variant_m"] == pytest.approx(99.999, abs=1e-6)
    assert sp["added_length_m"] == pytest.approx(100.0, abs=1e-6)
