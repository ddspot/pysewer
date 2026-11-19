# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

from pathlib import Path

import pytest

import pysewer
from pysewer.optimization import min_self_cleansing_slope, needs_pump

TEST_DATA = Path("tests") / "test_data"
SINK = (691350, 2553250)


def _connection_graph():
    model_domain = pysewer.ModelDomain(
        TEST_DATA / "dem.tif",
        TEST_DATA / "roads_clipped.shp",
        TEST_DATA / "buildings_clipped.shp",
    )
    model_domain.add_sink(SINK)
    return model_domain.generate_connection_graph(), model_domain.get_sinks()


def _flags(graph, min_slope):
    """needs_pump of each edge, with the given minimum slope."""
    opt = pysewer.get_config().optimization
    return {
        (u, v): needs_pump(
            profile=data["profile"],
            min_slope=min_slope,
            tmax=opt.tmax,
            tmin=opt.tmin,
            inflow_trench_depth=opt.inflow_trench_depth,
        )[0]
        for u, v, data in graph.edges(data=True)
    }


@pytest.fixture
def config():
    yield
    pysewer.set_config()


def test_self_cleansing_uses_the_slope_of_the_smallest_pipe(config):
    pysewer.set_config(
        custom_settings_dict={"optimization": {"min_slope_model": "self_cleansing"}}
    )
    graph, _ = _connection_graph()
    slope = min_self_cleansing_slope(min(pysewer.get_config().optimization.diameters))
    flags = {(u, v): bool(data["needs_pump"]) for u, v, data in graph.edges(data=True)}
    assert flags == _flags(graph, slope)


def test_uniform_uses_min_slope(config):
    pysewer.set_config(
        custom_settings_dict={"optimization": {"min_slope_model": "uniform"}}
    )
    graph, _ = _connection_graph()
    flags = {(u, v): bool(data["needs_pump"]) for u, v, data in graph.edges(data=True)}
    assert flags == _flags(graph, pysewer.get_config().optimization.min_slope)


def test_each_designed_pump_edge_has_needs_pump(config):
    # needs_pump must not be less strict than the design step: each edge on
    # which the design step places a pump must have needs_pump, so that the
    # pump penalty applies to it.
    graph, sinks = _connection_graph()
    needs = {(u, v): bool(data["needs_pump"]) for u, v, data in graph.edges(data=True)}
    tree = pysewer.rsph_tree(graph, sinks)
    designed = pysewer.calculate_hydraulic_parameters(
        pysewer.estimate_peakflow(tree), sinks=sinks
    )
    pumped = [
        (u, v)
        for u, v in designed.edges
        if designed.edges[u, v].get("pressurized")
        and designed.nodes[u].get("pumping_station")
    ]
    assert pumped, "expected at least one pumping station in the test network"
    assert all(needs[edge] for edge in pumped)
