# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

import networkx as nx
import pytest

from pysewer.config.manager import reset_config, set_config
from pysewer.optimization import calculate_hydraulic_parameters, select_diameter_with_constraints


@pytest.fixture(autouse=True)
def restore_config():
    reset_config()
    yield
    reset_config()


def test_select_diameter_respects_depth_and_velocity():
    """
    Pick the smallest diameter that satisfies both d/D and velocity bounds.
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
    assert "no_diameter_meets_depth_or_velocity" in violations


def test_hydraulic_violations_flagged_when_no_diameter_fits():
    """
    If no diameter meets d/D or velocity bounds, the edge records a violation.
    """
    custom = {
        "optimization": {
            "diameters": [0.5],  # overly large pipe for the tiny flow below
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
