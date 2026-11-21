# SPDX-FileCopyrightText: 2025 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""network_metrics, quality_checks, summarize_findings (metrics/1)."""

import dataclasses
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import networkx as nx
import numpy as np
import pytest
from pyproj import CRS

import pysewer
from pysewer.bundle import NetworkBundle
from pysewer.config.manager import get_config, reset_config, set_config
from pysewer.helper import get_edge_gdf, get_sewer_info
from pysewer.metrics import network_metrics, quality_checks, summarize_findings
from tests.test_bundle import _synthetic

TEST_DATA = Path("tests") / "test_data"
SINK = (691350, 2553250)
COLUMNS = ["element_type", "element_id", "check", "severity", "value", "limit",
           "unit", "message"]

# thresholds used by every synthetic check (independent of settings.yaml)
CFG = {
    "tmax": 8.0, "tmin": 0.25, "min_cover": 0.25, "velocity_min": 0.7,
    "velocity_max": 3.0, "max_depth_ratio": 0.75, "min_pipe_length": 2.0,
    "small_sewer_flow_threshold": 0.001,
}


def cfg(**over):
    return {"optimization": {**CFG, **over}}


@pytest.fixture(autouse=True)
def _clean_config():
    reset_config()
    yield
    reset_config()


@pytest.fixture
def b():
    return NetworkBundle.from_graph(_synthetic(), name="syn")


def edit(b, pid, **cols):
    """Copy of the bundle with columns of one pipe replaced."""
    pipes = b.pipes.copy()
    for k, v in cols.items():
        pipes[k] = pipes[k].astype(object) if isinstance(v, str) else pipes[k]
        pipes.loc[pipes.pipe_id == pid, k] = v
    return dataclasses.replace(b, pipes=pipes)


def only(df, check):
    return df[df.check == check]


# Synthetic network (see test_bundle._synthetic):
#  P000001 N1->N2 gravity private  L=140 D=0.2 depth 1.5/2.0 peak .001 v .7  d/D .3
#  P000002 N2->N3 pressurized      L=120 D=0.1 depth 0.8/0.8 peak .002
#  P000003 N3->N4 gravity          L=140 D=0.3 depth 1.1/0.5 peak .003 v .9  d/D .4
#  P000004 N5->N4 undesigned       L=50
#  nodes: N1 building+private pump, N2 lifting, N3 pumping, N4 sink,
#         N5 building, N6 building unrouted (no pipe)
# ---------------------------------------------------------------- metrics
def test_metrics_synthetic_exact(b):
    m = network_metrics(b)
    assert m["schema"] == "metrics/1"
    assert (m["n_pipes"], m["n_nodes"]) == (4, 6)
    assert (m["buildings_total"], m["buildings_connected"],
            m["buildings_unconnected"]) == (3, 2, 1)
    # lengths 140 + 120 + 140 + 50 = 450
    assert m["total_length_m"] == pytest.approx(450.0, abs=1e-9)
    assert m["gravity_length_m"] == pytest.approx(280.0, abs=1e-9)  # 140 + 140
    assert m["pressurized_length_m"] == pytest.approx(120.0, abs=1e-9)
    assert m["undesigned_length_m"] == pytest.approx(50.0, abs=1e-9)
    assert m["private_length_m"] == pytest.approx(140.0, abs=1e-9)  # P1 only
    assert m["n_pumping_stations"] == 1  # N3 (N1 is a private pump)
    assert m["n_lifting_stations"] == 1
    assert m["n_private_pumps"] == 1
    assert m["diameter_count"] == {"0.1": 1, "0.2": 1, "0.3": 1}
    assert m["diameter_length_m"] == pytest.approx({"0.1": 120.0, "0.2": 140.0, "0.3": 140.0})
    assert m["diameter_count_gravity"] == {"0.2": 1, "0.3": 1}
    assert m["diameter_length_m_gravity"] == pytest.approx({"0.2": 140.0, "0.3": 140.0})
    assert m["max_depth_m"] == pytest.approx(2.0, abs=1e-9)  # max(1.5, 2.0, 1.1, 0.5)
    # (1.75*140 + 0.8*140) / 280 = (245 + 112) / 280 = 1.275
    assert m["mean_depth_m"] == pytest.approx(1.275, abs=1e-9)
    # 1.75*140 + 0.8*120 + 0.8*140 = 245 + 96 + 112 = 453 (gravity + pressurized)
    assert m["excavation_proxy_m3_per_m_width"] == pytest.approx(453.0, abs=1e-9)
    assert m["peak_flow_max_m3s"] == pytest.approx(0.003, abs=1e-12)
    assert m["max_d_over_D_gravity"] == pytest.approx(0.4, abs=1e-12)
    assert m["velocity_min_ms"] == pytest.approx(0.7, abs=1e-12)
    assert m["velocity_max_ms"] == pytest.approx(0.9, abs=1e-12)
    assert m["hydraulic_violations"] == {"max_velocity": 1, "min_slope": 1}
    assert m["constraint_violations"] == {"max_depth": 1, "min_slope": 1}
    assert m["n_components"] == 1  # N6 has no pipe and is not counted
    assert m["longest_flow_path_m"] == pytest.approx(400.0, abs=1e-9)  # 140+120+140
    assert m["n_sinks"] == 1


def test_metrics_json_able_and_python_types(b):
    m = network_metrics(b)
    text = json.dumps(m)
    assert json.loads(text) == m

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                assert isinstance(k, str)
                walk(v)
        else:
            assert type(x) in (int, float, str, type(None)), (type(x), x)

    walk(m)


def test_metrics_accepts_graph(b):
    assert network_metrics(_synthetic()) == network_metrics(b)


def test_metrics_type_error():
    with pytest.raises(TypeError):
        network_metrics("nope")


def test_metrics_empty_graph():
    m = network_metrics(NetworkBundle.from_graph(nx.DiGraph(crs=CRS("EPSG:32633"))))
    assert m["n_pipes"] == 0 and m["n_nodes"] == 0
    assert m["total_length_m"] == 0.0 and m["excavation_proxy_m3_per_m_width"] == 0.0
    assert m["diameter_count"] == {} and m["hydraulic_violations"] == {}
    assert m["max_depth_m"] is None and m["mean_depth_m"] is None
    assert m["peak_flow_max_m3s"] is None and m["velocity_min_ms"] is None
    assert m["n_components"] == 0 and m["longest_flow_path_m"] == 0.0
    assert m["n_sinks"] == 0
    json.dumps(m)


def test_metrics_single_gravity_pipe():
    from shapely.geometry import LineString

    from tests.test_bundle import A, B, _pairs

    G = nx.DiGraph(crs=CRS("EPSG:32633"))
    G.add_node(A, node_type="building", elevation=5.0)
    G.add_node(B, node_type="wwtp", elevation=4.0)
    G.add_edge(A, B, geometry=LineString([A, B]), diameter=0.2, pressurized=False,
               profile=_pairs((0, 5), (100, 4)),
               trench_depth_profile=_pairs((0, 4.0), (100, 3.0)),
               peak_flow=0.002, velocity=0.8, d_over_D=0.5)
    m = network_metrics(NetworkBundle.from_graph(G))
    assert m["total_length_m"] == pytest.approx(100.0, abs=1e-9)  # hypot(60, 80)
    assert m["mean_depth_m"] == pytest.approx(1.0, abs=1e-9)  # (1 + 1) / 2
    assert m["max_depth_m"] == pytest.approx(1.0, abs=1e-9)
    assert m["excavation_proxy_m3_per_m_width"] == pytest.approx(100.0, abs=1e-9)  # 1 * 100
    assert m["longest_flow_path_m"] == pytest.approx(100.0, abs=1e-9)
    assert (m["n_components"], m["n_sinks"], m["buildings_connected"]) == (1, 1, 1)
    assert m["velocity_min_ms"] == m["velocity_max_ms"] == pytest.approx(0.8)


def test_metrics_nan_depth_skipped():
    # only pipe with finite depths counts for mean depth and excavation
    G = _synthetic()
    b0 = NetworkBundle.from_graph(G)
    b1 = edit(b0, "P000001", depth_up=np.nan, depth_down=np.nan)
    m = network_metrics(b1)
    assert m["mean_depth_m"] == pytest.approx(0.8, abs=1e-9)  # P3 only: (1.1+0.5)/2
    assert m["excavation_proxy_m3_per_m_width"] == pytest.approx(208.0, abs=1e-9)  # 96 + 112
    assert m["max_depth_m"] == pytest.approx(1.1, abs=1e-9)


def test_metrics_all_nan_gravity_depth_is_none(b):
    b1 = edit(edit(b, "P000001", depth_up=np.nan, depth_down=np.nan),
              "P000003", depth_up=np.nan, depth_down=np.nan)
    m = network_metrics(b1)
    assert m["mean_depth_m"] is None and m["max_depth_m"] is None
    assert m["excavation_proxy_m3_per_m_width"] == pytest.approx(96.0, abs=1e-9)  # 0.8*120


def test_metrics_no_gravity_pipes():
    b0 = NetworkBundle.from_graph(_synthetic())
    pipes = b0.pipes[b0.pipes.pipe_type != "gravity"]
    m = network_metrics(dataclasses.replace(b0, pipes=pipes))
    assert m["gravity_length_m"] == 0.0
    assert m["mean_depth_m"] is None and m["max_d_over_D_gravity"] is None
    assert m["velocity_min_ms"] is None and m["diameter_count_gravity"] == {}


def test_metrics_violation_counts_pipes_not_items(b):
    # a pipe listing the same item twice counts once
    b1 = edit(b, "P000003", hydraulic_violations="min_slope,min_slope",
              constraint_violations="")
    m = network_metrics(b1)
    assert m["hydraulic_violations"] == {"min_slope": 2, "max_velocity": 1}


def test_metrics_node_already_sink_and_two_components(b):
    # drop the pipe that joins N3 and N4 -> {N1,N2,N3} and {N4,N5}
    pipes = b.pipes[b.pipes.pipe_id != "P000003"]
    m = network_metrics(dataclasses.replace(b, pipes=pipes))
    assert m["n_components"] == 2
    assert m["longest_flow_path_m"] == pytest.approx(260.0, abs=1e-9)  # 140 + 120
    assert m["n_sinks"] == 1


def test_import_metrics_is_light():
    code = (
        "import sys, pysewer\n"
        "from pysewer.metrics import network_metrics\n"
        "bad = [m for m in ('matplotlib', 'osmnx') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------- quality_checks
def test_checks_synthetic_exact_with_explicit_config(b):
    f = quality_checks(b, cfg())
    assert list(f.columns) == COLUMNS
    got = list(zip(f.severity, f.element_id, f.check, strict=False))
    # errors, then warnings, then info; element_id inside each group
    assert got == [
        ("error", "N000006", "unrouted_building"),
        ("warning", "P000001", "engine:max_depth"),
        ("warning", "P000001", "engine:max_velocity"),
        ("warning", "P000001", "engine:min_slope"),
        ("warning", "P000003", "cover_below_min"),
        ("info", "P000004", "undesigned_pipe"),
    ]
    c = f.set_index("check")
    # P3: cover = min(1.1 - 0.3, 0.5 - 0.3) = 0.2 < 0.25
    row = c.loc["cover_below_min"]
    assert row.value == pytest.approx(0.2, abs=1e-9)
    assert row.limit == 0.25 and row.unit == "m" and row.element_type == "pipe"
    assert c.loc["unrouted_building", "element_type"] == "node"
    assert "crown" in row.message
    assert f.message.map(bool).all()


def test_checks_default_global_config(b):
    # defaults: tmax 8, min_cover 0.25 -> only P3 cover; velocity 0.7 is not < 0.7
    f = quality_checks(b)
    assert list(only(f, "cover_below_min").element_id) == ["P000003"]
    assert only(f, "depth_exceeds_tmax").empty
    assert only(f, "velocity_low").empty


def test_checks_clean_network_is_empty_with_columns():
    G = _synthetic()
    b0 = NetworkBundle.from_graph(G)
    pipes = b0.pipes[b0.pipes.pipe_id == "P000001"].copy()
    pipes["hydraulic_violations"] = ""
    pipes["constraint_violations"] = ""
    nodes = b0.nodes[b0.nodes.role != "building"]
    f = quality_checks(dataclasses.replace(b0, pipes=pipes, nodes=nodes), cfg())
    assert f.empty and list(f.columns) == COLUMNS


def test_checks_empty_graph():
    f = quality_checks(NetworkBundle.from_graph(nx.DiGraph(crs=CRS("EPSG:32633"))), cfg())
    assert f.empty and list(f.columns) == COLUMNS


def test_checks_accept_graph():
    a = quality_checks(_synthetic(), cfg())
    c = quality_checks(NetworkBundle.from_graph(_synthetic()), cfg())
    assert a.equals(c)


# boundary: (check, column edits at exactly the limit, edits just beyond, value)
@pytest.mark.parametrize(
    "check,at,beyond,value",
    [
        ("depth_exceeds_tmax", {"depth_up": 8.0}, {"depth_up": 8.01}, 8.01),
        ("depth_exceeds_tmax", {"depth_down": 8.0}, {"depth_down": 8.01}, 8.01),
        # cover = depth - D (D = 0.3): 0.55 - 0.3 = 0.25 at the limit
        ("cover_below_min", {"depth_up": 0.55, "depth_down": 0.55},
         {"depth_up": 0.54, "depth_down": 0.9}, 0.24),
        ("cover_below_min", {"depth_up": 0.9, "depth_down": 0.55},
         {"depth_up": 0.9, "depth_down": 0.54}, 0.24),
        ("velocity_low", {"velocity": 0.7}, {"velocity": 0.69}, 0.69),
        ("velocity_high", {"velocity": 3.0}, {"velocity": 3.01}, 3.01),
        ("fill_above_target", {"d_over_D": 0.75}, {"d_over_D": 0.76}, 0.76),
        ("invert_rises", {"invert_slope": 0.0}, {"invert_slope": 0.01}, 0.01),
        ("invert_rises", {"invert_slope": 1e-10}, {"invert_slope": 1e-6}, 1e-6),
        ("short_pipe", {"length": 2.0}, {"length": 1.99}, 1.99),
    ],
)
def test_check_boundary(b, check, at, beyond, value):
    # P000003 (gravity, D 0.3, depth 1.1/0.5 by default); neutral depths first
    base = {"depth_up": 2.0, "depth_down": 2.0}
    neutral = edit(b, "P000003", **{**base, **at})
    hit = edit(b, "P000003", **{**base, **beyond})
    sub = only(quality_checks(neutral, cfg()), check)
    assert sub[sub.element_id == "P000003"].empty, sub
    sub = only(quality_checks(hit, cfg()), check)
    sub = sub[sub.element_id == "P000003"]
    assert len(sub) == 1, sub
    assert sub.iloc[0].value == pytest.approx(value, rel=1e-6, abs=1e-12)


def test_check_severities(b):
    sev = {}
    big = edit(b, "P000003", depth_up=9.0, velocity=0.1, d_over_D=0.9,
               invert_slope=0.1, length=1.0)
    f = quality_checks(big, cfg(velocity_max=0.05))
    for chk, s in zip(f.check, f.severity, strict=True):
        sev[chk] = s
    assert sev["depth_exceeds_tmax"] == "error"
    assert sev["cover_below_min"] == "warning"  # min(9.0, 0.5) - 0.3 = 0.2
    assert sev["velocity_low"] == "warning"
    assert sev["velocity_high"] == "warning"
    assert sev["fill_above_target"] == "error"
    assert sev["invert_rises"] == "error"
    assert sev["short_pipe"] == "info"
    assert sev["unrouted_building"] == "error"
    assert sev["undesigned_pipe"] == "info"
    assert sev["engine:min_slope"] == "warning"
    # value is the worse end depth: max(9.0, 0.5)
    assert only(f, "depth_exceeds_tmax").iloc[0].value == pytest.approx(9.0)


def test_velocity_low_needs_flow_above_threshold(b):
    low = {"velocity": 0.5}
    # peak 0.003 vs threshold 0.003: exactly at -> finding (>=)
    f = quality_checks(edit(b, "P000003", **low), cfg(small_sewer_flow_threshold=0.003))
    assert list(only(f, "velocity_low").element_id) == ["P000003"]
    # peak 0.003 < threshold 0.0031 -> no finding
    f = quality_checks(edit(b, "P000003", **low), cfg(small_sewer_flow_threshold=0.0031))
    assert only(f, "velocity_low").empty


def test_pressurized_pipe_not_checked_as_gravity(b):
    # P2 is pressurized: bad velocity/fill/slope/depth must not be reported
    b1 = edit(b, "P000002", velocity=0.01, d_over_D=1.0, invert_slope=0.5,
              depth_up=20.0, depth_down=20.0)
    f = quality_checks(b1, cfg())
    assert "P000002" not in set(f.element_id)


def test_short_pipe_applies_to_all_pipe_types(b):
    b1 = edit(edit(b, "P000002", length=1.0), "P000004", length=0.5)
    f = only(quality_checks(b1, cfg()), "short_pipe")
    assert list(f.element_id) == ["P000002", "P000004"]
    assert list(f.value) == [1.0, 0.5]


def test_nan_never_produces_findings(b):
    cols = dict.fromkeys(["depth_up", "depth_down", "velocity", "d_over_D",
                          "invert_slope", "peak_flow", "diameter"], np.nan)
    f = quality_checks(edit(b, "P000003", **cols), cfg())
    assert "P000003" not in set(f.element_id)
    assert not f.value.dropna().isna().any()


def test_nan_in_undesigned_pipe_gives_only_info(b):
    f = quality_checks(b, cfg())
    assert set(f[f.element_id == "P000004"].check) == {"undesigned_pipe"}


def test_engine_rows_one_per_pipe_item(b):
    f = quality_checks(b, cfg())
    eng = f[f.check.str.startswith("engine:")]
    # hydraulic {max_velocity, min_slope} + constraint {min_slope, max_depth}
    # -> union of three items on P1, min_slope once
    assert sorted(eng.check) == ["engine:max_depth", "engine:max_velocity", "engine:min_slope"]
    assert (eng.element_id == "P000001").all()
    assert (eng.severity == "warning").all()
    assert eng.value.isna().all() and eng.limit.isna().all()


def test_unrouted_building_rows(b):
    f = only(quality_checks(b, cfg()), "unrouted_building")
    assert list(f.element_id) == ["N000006"] and list(f.element_type) == ["node"]
    # a building without a pipe, not flagged unrouted, is reported as well
    nodes = b.nodes.copy()
    nodes.loc[nodes.node_id == "N000006", "unrouted"] = False
    f = only(quality_checks(dataclasses.replace(b, nodes=nodes), cfg()), "unrouted_building")
    assert list(f.element_id) == ["N000006"]


def test_sorting_severity_then_element_id(b):
    b1 = edit(edit(b, "P000003", depth_up=9.0), "P000001", depth_up=9.5)
    f = quality_checks(b1, cfg())
    order = {"error": 0, "warning": 1, "info": 2}
    keys = [(order[s], e) for s, e in zip(f.severity, f.element_id, strict=True)]
    assert keys == sorted(keys)
    errs = f[f.severity == "error"]
    # errors: N000006 (unrouted), P000001 (9.5 m), P000003 (9.0 m); N < P
    assert list(errs.element_id) == ["N000006", "P000001", "P000003"]


def test_config_sources(b):
    # dict (full and section only) change thresholds
    base = quality_checks(b, cfg())
    tight = quality_checks(b, cfg(tmax=1.0))
    # worse end depths 2.0 (P1) and 1.1 (P3) both exceed 1.0
    assert set(only(tight, "depth_exceeds_tmax").element_id) == {"P000001", "P000003"}
    assert only(base, "depth_exceeds_tmax").empty
    sect = quality_checks(b, {**CFG, "tmax": 1.0})
    assert sect.equals(tight)
    # global config
    set_config(custom_settings_dict={"optimization": {"tmax": 1.0}})
    glob = quality_checks(b)
    assert not only(glob, "depth_exceeds_tmax").empty
    # Config object
    assert quality_checks(b, get_config()).equals(glob)
    reset_config()
    assert only(quality_checks(b), "depth_exceeds_tmax").empty


def test_config_bundle_uses_metadata(b):
    b1 = dataclasses.replace(b, metadata={**b.metadata, "config": {
        **b.metadata["config"],
        "optimization": {**b.metadata["config"]["optimization"], "tmax": 1.0}}})
    f = quality_checks(b1, config="bundle")
    assert not only(f, "depth_exceeds_tmax").empty
    # global config is untouched by the bundle one
    assert only(quality_checks(b1), "depth_exceeds_tmax").empty
    with pytest.raises(ValueError):
        quality_checks(b, config="nonsense")


# -------------------------------------------------------------- summarize
def test_summarize_empty():
    assert summarize_findings(quality_checks(NetworkBundle.from_graph(
        nx.DiGraph(crs=CRS("EPSG:32633"))), cfg()), cfg()) == []


def test_summarize_contract_and_order(b):
    f = quality_checks(b, cfg())
    s = summarize_findings(f, cfg())
    assert all(set(d) == {"check", "severity", "count", "message"} for d in s)
    assert [(d["check"], d["severity"], d["count"]) for d in s] == [
        ("unrouted_building", "error", 1),
        ("cover_below_min", "warning", 1),
        ("engine:max_depth", "warning", 1),
        ("engine:max_velocity", "warning", 1),
        ("engine:min_slope", "warning", 1),
        ("undesigned_pipe", "info", 1),
    ]


def test_summarize_count_desc_within_severity(b):
    b1 = edit(edit(b, "P000003", velocity=3.5), "P000001", velocity=3.5)
    s = summarize_findings(quality_checks(b1, cfg()), cfg())
    warn = [d for d in s if d["severity"] == "warning"]
    assert warn[0]["check"] == "velocity_high" and warn[0]["count"] == 2
    counts = [d["count"] for d in warn]
    assert counts == sorted(counts, reverse=True)


def test_summarize_singular_plural(b):
    s = {d["check"]: d["message"] for d in summarize_findings(quality_checks(b, cfg()), cfg())}
    assert s["unrouted_building"].startswith("1 node:")  # "node", never "1 nodes"
    assert "1 pipe have" not in s["cover_below_min"]
    assert s["cover_below_min"].startswith("1 pipe has ")
    b1 = edit(edit(b, "P000003", velocity=3.5), "P000001", velocity=3.5)
    s2 = {d["check"]: d["message"] for d in
          summarize_findings(quality_checks(b1, cfg()), cfg())}
    assert s2["velocity_high"].startswith("2 pipes")
    # cover with several pipes: plural verb
    b2 = edit(edit(b, "P000003", depth_down=0.4), "P000001", depth_up=0.3)
    s3 = {d["check"]: d["message"] for d in
          summarize_findings(quality_checks(b2, cfg()), cfg())}
    assert s3["cover_below_min"].startswith("2 pipes have ")


def test_summarize_cover_message_explains_crown_vs_tmin(b):
    msg = {d["check"]: d["message"] for d in
           summarize_findings(quality_checks(b, cfg()), cfg())}["cover_below_min"]
    assert "crown" in msg and "tmin" in msg
    assert "0.25" in msg  # min_cover and tmin both 0.25 in cfg
    msg2 = {d["check"]: d["message"] for d in summarize_findings(
        quality_checks(b, cfg(tmin=0.6)), cfg(tmin=0.6))}["cover_below_min"]
    assert "0.6" in msg2


def test_summarize_bundle_config(b):
    f = quality_checks(b, "bundle")
    s = summarize_findings(f, "bundle", bundle=b)
    assert s
    with pytest.raises(ValueError):
        summarize_findings(f, "bundle")


# ------------------------------------------------------------- real network
@pytest.fixture(scope="module")
def designed():
    reset_config()
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


def test_real_metrics_match_sewer_info(designed, real):
    G, _ = designed
    info = get_sewer_info(G)
    m = network_metrics(real)
    assert m["gravity_length_m"] == pytest.approx(info["Gravity Sewer Length [m]"], abs=1.0)
    assert m["pressurized_length_m"] == pytest.approx(
        info["Pressurized Sewer Length [m]"], abs=1.0)
    assert m["n_pumping_stations"] == info["Pumping Stations"]
    assert m["n_lifting_stations"] == info["Lifting Stations"]
    assert m["n_private_pumps"] == info["Private Pumps"]
    assert m["buildings_total"] == info["Total Buildings"]
    assert m["buildings_unconnected"] == info["Unconnected Buildings"]
    assert m["n_pipes"] == G.number_of_edges()
    assert m["n_sinks"] >= 1 and m["n_components"] >= 1
    assert m["total_length_m"] == pytest.approx(
        m["gravity_length_m"] + m["pressurized_length_m"] + m["undesigned_length_m"],
        abs=1e-6)
    assert sum(m["diameter_count"].values()) == int(real.pipes.diameter.notna().sum())
    assert m["longest_flow_path_m"] <= m["total_length_m"] + 1e-6
    assert m["longest_flow_path_m"] > 0
    json.dumps(m)


def test_real_findings_plausible(real):
    f = quality_checks(real)
    assert list(f.columns) == COLUMNS
    assert len(only(f, "cover_below_min")) > 0
    assert only(f, "cover_below_min").severity.eq("warning").all()
    assert only(f, "cover_below_min").value.lt(0.25).all()
    # the engine enforces the limit it designs to
    assert only(f, "depth_exceeds_tmax").empty
    assert only(f, "invert_rises").empty
    s = summarize_findings(f)
    assert sum(d["count"] for d in s) == len(f)
    assert {d["check"] for d in s} == set(f.check)


# ----------------------------------------------------------- run_scenarios
def _load_script():
    path = Path("benchmarks") / "scripts" / "run_scenarios.py"
    spec = importlib.util.spec_from_file_location("run_scenarios_under_test", path.resolve())
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run_scenario_matches_old_formulas():
    scenario = {
        "name": "unit",
        "description": "unit test",
        "data": {
            "dem": "tests/test_data/dem.tif",
            "roads": "tests/test_data/roads_clipped.shp",
            "buildings": "tests/test_data/buildings_clipped.shp",
            "sink": list(SINK),
        },
        "config": {"optimization": {"tmax": 8.0}},
    }
    new = _load_script().run_scenario(scenario)

    # reference: the pre-refactor computation (git show HEAD:benchmarks/scripts/run_scenarios.py)
    reset_config()
    set_config(custom_settings_dict=scenario["config"])
    md = pysewer.ModelDomain(
        TEST_DATA / "dem.tif", TEST_DATA / "roads_clipped.shp",
        TEST_DATA / "buildings_clipped.shp")
    md.add_sink(SINK)
    cg = md.generate_connection_graph()
    layout = pysewer.rsph_tree(cg, [SINK])
    sewer = pysewer.estimate_peakflow(layout)
    G = pysewer.calculate_hydraulic_parameters(sewer, sinks=[SINK])
    pipes = get_edge_gdf(G, detailed=True)
    gravity = pipes[~pipes["pressurized"]]
    violations = pipes["hydraulic_violations"].explode().dropna()
    max_fill = float(gravity["d_over_D"].max()) if len(gravity) else 0.0
    old = {
        "scenario": "unit",
        "description": "unit test",
        "config_overrides": scenario["config"],
        "n_pipes": len(pipes),
        "total_length_m": round(float(pipes.geometry.length.sum()), 1),
        "pressurized_length_m": round(
            float(pipes[pipes["pressurized"]].geometry.length.sum()), 1),
        "n_pumping_stations": sum(
            1 for _, d in G.nodes(data=True) if d.get("pumping_station")),
        "n_lifting_stations": sum(
            1 for _, d in G.nodes(data=True) if d.get("lifting_station")),
        "diameter_distribution": {
            str(k): int(v) for k, v in sorted(pipes["diameter"].value_counts().items())},
        "diameter_distribution_gravity": {
            str(k): int(v) for k, v in sorted(gravity["diameter"].value_counts().items())},
        "hydraulic_violations": {
            str(k): int(v) for k, v in violations.value_counts().items()},
        "peak_flow_max_m3s": round(float(pipes["peak_flow"].max()), 6),
        "max_capacity_ratio_gravity": round(max_fill, 4),
    }
    assert list(new) == list(old)  # same keys, same order
    assert new == old
    json.dumps(new)
