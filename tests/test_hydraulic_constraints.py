# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

import networkx as nx
import pytest

from pysewer.config.manager import reset_config, set_config
from pysewer.optimization import (
    calculate_hydraulic_parameters,
    select_diameter_with_constraints,
)


@pytest.fixture(autouse=True)
def restore_config():
    reset_config()
    yield
    reset_config()


def test_select_diameter_respects_depth_and_velocity():
    """
    Pick the smallest diameter whose capacity satisfies the depth ratio;
    velocity within bounds leaves no violations.
    """
    diam, violations = select_diameter_with_constraints(
        target_flow=0.02,
        diameters=[0.1, 0.15, 0.2],
        roughness=0.013,
        slope=-0.01,
        max_depth_ratio=0.75,
        vmin=0.7,
        vmax=3.0,
    )
    assert diam == 0.2
    assert violations == []


def test_select_diameter_capacity_shortfall_flagged():
    """
    If no diameter has sufficient capacity, the largest is returned with a
    depth-ratio violation recorded.
    """
    diam, violations = select_diameter_with_constraints(
        target_flow=0.02,
        diameters=[0.1, 0.15],
        roughness=0.013,
        slope=-0.01,
        max_depth_ratio=0.75,
        vmin=0.7,
        vmax=3.0,
    )
    assert diam == 0.15
    assert "no_diameter_meets_depth_ratio" in violations


def test_select_diameter_low_flow_flags_velocity_not_size():
    """
    A tiny flow must not inflate the pipe size (regression: the old logic
    fell back to max(diameters) because no pipe could meet vmin); it selects
    the smallest adequate pipe and records a velocity_min violation.
    """
    diam, violations = select_diameter_with_constraints(
        target_flow=0.001,
        diameters=[0.2, 0.3, 2.0],
        roughness=0.013,
        slope=-0.005,
        max_depth_ratio=0.75,
        vmin=0.7,
        vmax=3.0,
    )
    assert diam == 0.2
    assert "velocity_min" in violations


def test_full_flow_manning_back_of_envelope():
    """
    Hand calculation, DN300 at 1% slope, Manning n = 0.013:
      A  = pi * 0.15^2          = 0.070686 m^2
      Rh = D/4                  = 0.075 m
      v  = (1/n) * Rh^(2/3) * S^0.5
         = 76.92 * 0.17787 * 0.1 = 1.368 m/s
      Q  = A * v                = 0.0967 m^3/s  (~97 L/s)
    """
    from pysewer.optimization import _full_flow_manning

    assert _full_flow_manning(0.3, 0.013, -0.01) == pytest.approx(0.0967, abs=0.0005)


def test_partial_flow_capacity_geometry_identities():
    """Exact circular-pipe geometry: Q(1.0)=Q_full, Q(0.5)=Q_full/2 (same
    hydraulic radius), Q(0.75)~0.912*Q_full (Butler's d/D=0.75 criterion)."""
    from pysewer.optimization import _full_flow_manning, _partial_flow_capacity

    D, n, S = 0.3, 0.013, -0.01
    q_full = _full_flow_manning(D, n, S)
    assert _partial_flow_capacity(D, n, S, 1.0) == pytest.approx(q_full)
    assert _partial_flow_capacity(D, n, S, 0.5) == pytest.approx(0.5 * q_full)
    assert _partial_flow_capacity(D, n, S, 0.75) / q_full == pytest.approx(
        0.912, abs=0.002
    )


def test_proportional_depth_roundtrip():
    """d/D -> capacity -> d/D closes: the exported d_over_D is a true depth."""
    from pysewer.optimization import _partial_flow_capacity, _proportional_depth

    D, n, S = 0.3, 0.013, -0.01
    for target in (0.3, 0.6, 0.75):
        q = _partial_flow_capacity(D, n, S, target)
        assert _proportional_depth(D, n, S, q) == pytest.approx(target, abs=0.01)
    # boundary behavior
    assert _proportional_depth(D, n, S, 0.0) == 0.0
    from pysewer.optimization import _full_flow_manning

    assert _proportional_depth(D, n, S, 1.5 * _full_flow_manning(D, n, S)) == 1.0


def test_capacity_criterion_is_depth_not_flow_ratio():
    """
    Butler: d/D <= 0.75 corresponds to Q/Q_full <= 0.912. A design flow of
    0.90*Q_full therefore fits (the old flow-ratio check Q/Q_full <= 0.75
    would wrongly have stepped up a size); 0.95*Q_full does not fit.
    """
    from pysewer.optimization import _full_flow_manning

    q_full = _full_flow_manning(0.3, 0.013, -0.01)
    diam, _ = select_diameter_with_constraints(
        target_flow=0.90 * q_full,
        diameters=[0.3, 0.4],
        roughness=0.013,
        slope=-0.01,
        max_depth_ratio=0.75,
        vmin=0.7,
        vmax=3.0,
    )
    assert diam == 0.3
    diam2, _ = select_diameter_with_constraints(
        target_flow=0.95 * q_full,
        diameters=[0.3, 0.4],
        roughness=0.013,
        slope=-0.01,
        max_depth_ratio=0.75,
        vmin=0.7,
        vmax=3.0,
    )
    assert diam2 == 0.4


def test_peak_factor_formulas_back_of_envelope():
    """
    Hand checks against Butler Table 9.4 (P in thousands):
      Babbitt 5/P^0.2:  P=1k -> 5.00;  P=250k -> 5/250^0.2 = 1.66 (Butler
      Example 9.2);  P=100k -> 1.99
      Harman 1+14/(4+sqrt(P)): P=100k -> 1+14/(4+10) = 2.00
      Cap: P=10 people -> Babbitt 12.6 raw, capped to peak_factor_max=6.
    """
    from pysewer.optimization import peak_factor_for_population as pf

    assert pf(1000, method="babbitt") == pytest.approx(5.0)
    assert pf(250_000, method="babbitt") == pytest.approx(1.66, abs=0.01)
    assert pf(100_000, method="babbitt") == pytest.approx(1.99, abs=0.01)
    assert pf(100_000, method="harman") == pytest.approx(2.0, abs=0.01)
    assert pf(10, method="babbitt") == 6.0  # capped (BS EN 752 multiple)
    assert pf(12345, method="constant", constant=4.0) == 4.0


def test_estimate_peakflow_variable_pf_and_infiltration():
    """
    100 people, G=0.15 m3/hd.d, babbitt (capped at 6 for such a small
    population), infiltration 10% of domestic flow, unfactored:
      Q_peak = (6*15 + 0.1*15)/86400 = 0.0010590 m3/s
    """
    from pysewer.optimization import estimate_peakflow

    set_config(
        custom_settings_dict={
            "optimization": {
                "peak_factor_method": "babbitt",
                "infiltration_fraction": 0.1,
            }
        }
    )
    G = nx.DiGraph()
    G.add_node("b1", node_type="building")
    G.add_node("s1", node_type="sink")
    G.add_edge("b1", "s1")
    estimate_peakflow(G, inhabitants_dwelling=100, daily_wastewater_person=0.15)

    expected = (6 * 15 + 0.1 * 15) / 86400.0
    assert G.nodes["b1"]["peak_flow"] == pytest.approx(expected)
    assert G.nodes["b1"]["peak_factor"] == 6.0


def _design_single_edge(peak_flow, terrain_slope, config_overrides=None):
    """Run calculate_hydraulic_parameters on one gravity edge and return its
    hydraulic violations."""
    from shapely.geometry import LineString

    overrides = {"optimization": {"diameters": [0.2, 0.3]}}
    if config_overrides:
        overrides["optimization"].update(config_overrides)
    set_config(custom_settings_dict=overrides)

    G = nx.DiGraph()
    G.add_node("a", peak_flow=peak_flow)
    G.add_node("b")
    G.add_edge(
        "a",
        "b",
        profile=[(0, 10.0), (100.0, 10.0 + 100 * terrain_slope)],
        needs_pump=False,
        geometry=LineString([(0, 0), (100, 0)]),
    )
    calculate_hydraulic_parameters(G, sinks=["b"])
    return G.edges[("a", "b")]["hydraulic_violations"]


def test_dts_suppresses_velocity_min_at_intermittent_heads():
    """
    Butler ch. 9.4: below the small-sewer flow threshold (1 L/s) the steady
    velocity_min check does not apply; the deemed-to-satisfy minimum
    gradient (1:150) is checked instead.
    """
    # tiny flow, design gradient 1% (>= 1:150): clean — no velocity_min
    violations = _design_single_edge(0.0001, -0.01)
    assert "velocity_min" not in violations
    assert "gradient_below_dts" not in violations

    # tiny flow, gentle terrain and min_slope 1:500 (< 1:150): DTS flag,
    # still no meaningless velocity_min
    violations = _design_single_edge(
        0.0001, -0.002, config_overrides={"min_slope": -0.002}
    )
    assert "gradient_below_dts" in violations
    assert "velocity_min" not in violations

    # above the threshold the steady-flow velocity check applies as before
    violations = _design_single_edge(0.002, -0.002, config_overrides={"min_slope": -0.002})
    assert "velocity_min" in violations
    assert "gradient_below_dts" not in violations


def test_hydraulic_violations_flagged_when_no_diameter_fits():
    """
    If no diameter meets d/D or velocity bounds, the edge records a violation.
    """
    custom = {
        "optimization": {
            "diameters": [0.5],  # overly large pipe for the tiny flow below
            "roughness": 0.013,  # realistic Manning n so low fill means low velocity
            "velocity_min": 1.0,
            "velocity_max": 2.0,
            "max_depth_ratio": 0.5,
        }
    }
    set_config(custom_settings_dict=custom)

    G = nx.DiGraph()
    G.add_node("a", peak_flow=0.001)
    G.add_node("b")
    # Simple profile: 0m at 10m elev to 10m at 9.9m elev (slope -0.01)
    from shapely.geometry import LineString

    G.add_edge(
        "a",
        "b",
        profile=[(0, 10.0), (10.0, 9.9)],
        needs_pump=False,
        geometry=LineString([(0, 0), (10, 0)]),
    )

    calculate_hydraulic_parameters(G, sinks=["b"])

    edge_data = G.edges[("a", "b")]
    assert "hydraulic_violations" in edge_data
    assert "velocity_min" in edge_data["hydraulic_violations"] or "depth_ratio" in edge_data["hydraulic_violations"]
