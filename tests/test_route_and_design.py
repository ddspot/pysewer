# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

from pathlib import Path

import geopandas as gpd
import pytest

import pysewer

TEST_DATA = Path("tests") / "test_data"
EXAMPLE_DATA = Path("notebooks") / "example_data"


def _connection_graph(dem, roads, buildings, sink):
    model_domain = pysewer.ModelDomain(dem, roads, buildings)
    model_domain.add_sink(sink)
    return model_domain.generate_connection_graph(), model_domain.get_sinks()


@pytest.fixture(scope="module")
def small_case():
    return _connection_graph(
        TEST_DATA / "dem.tif",
        TEST_DATA / "roads_clipped.shp",
        TEST_DATA / "buildings_clipped.shp",
        (691350, 2553250),
    )


@pytest.fixture(scope="module")
def example_case():
    # The example shapefiles have a compound CRS (UTM 40N + unknown
    # vertical datum); the coordinates are EPSG:32640.
    def read(path):
        return gpd.read_file(EXAMPLE_DATA / path).set_crs(32640, allow_override=True)

    return _connection_graph(
        EXAMPLE_DATA / "1_DEM" / "dem_10m1.tif",
        read("3_Roads/roads_projected.shp"),
        read("2_Buildings/buildings_projected.shp"),
        (690500, 2557000),
    )


def _design(connection_graph, sinks):
    tree = pysewer.rsph_tree(connection_graph, sinks)
    return pysewer.calculate_hydraulic_parameters(
        pysewer.estimate_peakflow(tree), sinks=sinks
    )


def test_one_iteration_gives_the_plain_result(small_case):
    connection_graph, sinks = small_case
    plain = _design(connection_graph, sinks)
    designed = pysewer.route_and_design(connection_graph, sinks, max_iterations=1)
    assert set(designed.edges) == set(plain.edges)
    assert pysewer.get_sewer_info(designed) == pysewer.get_sewer_info(plain)
    assert designed.graph["station_aware"]["best"] == 0
    assert len(designed.graph["station_aware"]["iterations"]) == 1


def test_connection_graph_does_not_change(small_case):
    connection_graph, sinks = small_case
    weights = {
        (u, v, k): d["weight"]
        for u, v, k, d in connection_graph.edges(keys=True, data=True)
    }
    pysewer.route_and_design(connection_graph, sinks, station_cost=10_000)
    after = {
        (u, v, k): d["weight"]
        for u, v, k, d in connection_graph.edges(keys=True, data=True)
    }
    assert after == weights


STATION_COST = 500


@pytest.fixture(scope="module")
def example_designed(example_case):
    connection_graph, sinks = example_case
    return pysewer.route_and_design(
        connection_graph, sinks, station_cost=STATION_COST, max_iterations=4
    )


def test_returns_the_iteration_with_the_lowest_cost(example_designed):
    designed = example_designed
    records = designed.graph["station_aware"]["iterations"]
    best = designed.graph["station_aware"]["best"]
    for record in records:
        stations = record["pumping_stations"] + record["lifting_stations"]
        assert record["cost"] == pytest.approx(
            record["length"] + STATION_COST * stations
        )
    assert records[best]["cost"] == min(record["cost"] for record in records)

    info = pysewer.get_sewer_info(designed)
    assert info["Pumping Stations"] == records[best]["pumping_stations"]
    assert info["Lifting Stations"] == records[best]["lifting_stations"]


def test_station_cost_decreases_the_stations(example_designed):
    # Regression test for the pump penalty problem (TODO.md, after 0.4.0):
    # on a network with loops, rerouting around the stations must give
    # fewer stations than the plain routing.
    designed = example_designed
    records = designed.graph["station_aware"]["iterations"]
    best = records[designed.graph["station_aware"]["best"]]

    def stations(record):
        return record["pumping_stations"] + record["lifting_stations"]

    assert stations(best) < stations(records[0])


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"station_cost": -1}, "station_cost"), ({"max_iterations": 0}, "max_iterations")],
)
def test_invalid_arguments(small_case, kwargs, message):
    connection_graph, sinks = small_case
    with pytest.raises(ValueError, match=message):
        pysewer.route_and_design(connection_graph, sinks, **kwargs)


@pytest.mark.parametrize(
    "settings",
    [{"station_cost": -1}, {"station_max_iterations": 0}],
)
def test_invalid_config(settings):
    with pytest.raises(ValueError, match="station"):
        pysewer.set_config(custom_settings_dict={"optimization": settings})
