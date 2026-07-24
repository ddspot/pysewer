# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the features contributed via the ELAN project fork
(github.com/Djedouas/pysewer, branch elan-sprint-18):

- inhabitants number via a custom buildings attribute (estimate_peakflow)
- multi-layer GPKG export with explicit layer name and CRS
- fiona schema mapping for list and bool dtypes
- sink_coords attribute propagated to all routed elements
- total_static_head on pumping and lifting stations
"""

from pathlib import Path

import fiona
import geopandas as gpd
import networkx as nx
import pytest
from shapely.geometry import Point

import pysewer
from pysewer.export import is_list, map_dtype_to_fiona, write_gdf_to_gpkg
from pysewer.optimization import estimate_peakflow


def test_map_dtype_to_fiona_bool():
    assert map_dtype_to_fiona("bool") == "bool"
    assert map_dtype_to_fiona("int64") == "int"
    assert map_dtype_to_fiona("float64") == "float"
    assert map_dtype_to_fiona("object") == "str"


def test_is_list_detection():
    gdf = gpd.GeoDataFrame(
        {
            "lists": [[1, 2], [3]],
            "scalars": [1, 2],
            "geometry": [Point(0, 0), Point(1, 1)],
        }
    )
    assert is_list(gdf["lists"])
    assert not is_list(gdf["scalars"])


def test_write_gdf_to_gpkg_multiple_layers_and_crs(tmp_path):
    """Two layers written to the same GPKG keep their name, CRS and data."""
    out = tmp_path / "network.gpkg"
    crs = "EPSG:32632"
    nodes = gpd.GeoDataFrame(
        {"pressurized": [True, False], "geometry": [Point(0, 0), Point(1, 1)]},
        crs=crs,
    )
    edges = gpd.GeoDataFrame(
        {"profile": [[(0.0, 1.0), (1.0, 2.0)]], "geometry": [Point(2, 2)]},
        crs=crs,
    )

    write_gdf_to_gpkg(nodes, str(out), layer="nodes", crs=crs)
    write_gdf_to_gpkg(edges, str(out), layer="edges", crs=crs)

    assert set(fiona.listlayers(str(out))) == {"nodes", "edges"}
    nodes_rt = gpd.read_file(out, layer="nodes")
    assert len(nodes_rt) == 2
    assert nodes_rt.crs is not None and nodes_rt.crs.to_epsg() == 32632
    # bool dtype survives the fiona schema mapping
    assert list(nodes_rt["pressurized"]) == [True, False]
    edges_rt = gpd.read_file(out, layer="edges")
    # list-of-tuples column is serialized to a JSON string
    assert isinstance(edges_rt["profile"].iloc[0], str)


def test_estimate_peakflow_custom_inhabitants_attribute():
    """A custom attribute name takes priority over inhabitants_dwelling."""
    G = nx.DiGraph()
    G.add_node("b1", node_type="building", n_residents=12)
    G.add_node("s1", node_type="sink")
    G.add_edge("b1", "s1")

    estimate_peakflow(
        G,
        inhabitants_dwelling=2,
        inhabitants_dwelling_attribute_name="n_residents",
        daily_wastewater_person=0.15,
        peak_factor=4.0,
    )

    expected = (((12 * 0.15) / 24) * 4.0) / 3600
    assert pytest.approx(G.nodes["b1"]["peak_flow"]) == expected


class TestNetworkAttributes:
    """End-to-end checks on the small test network."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.sink_coordinates = (691350, 2553250)
        dem = Path("tests") / "test_data" / "dem.tif"
        buildings = Path("tests") / "test_data" / "buildings_clipped.shp"
        roads = Path("tests") / "test_data" / "roads_clipped.shp"
        model_domain = pysewer.ModelDomain(dem, roads, buildings)
        model_domain.add_sink(self.sink_coordinates)
        connection_graph = model_domain.generate_connection_graph()
        self.layout = pysewer.rsph_tree(connection_graph, [self.sink_coordinates])
        # node coordinates are normalized to floats within the graph
        self.expected_sink = str(tuple(float(c) for c in self.sink_coordinates))

    def test_sink_coords_on_all_routed_elements(self):
        routed_nodes = [n for n, deg in self.layout.degree() if deg > 0]
        for node in routed_nodes:
            assert self.layout.nodes[node]["sink_coords"] == self.expected_sink
        for _u, _v, data in self.layout.edges(data=True):
            assert data["sink_coords"] == self.expected_sink

    def test_total_static_head_on_stations(self):
        sewer_graph = pysewer.estimate_peakflow(
            self.layout, inhabitants_dwelling=6, daily_wastewater_person=250
        )
        G = pysewer.calculate_hydraulic_parameters(
            sewer_graph,
            sinks=[self.sink_coordinates],
            pressurized_diameter=0.2,
            diameters=[0.2, 0.3, 0.4, 0.5, 1, 2],
            roughness=0.012,
        )
        stations = [
            n
            for n, d in G.nodes(data=True)
            if d.get("pumping_station") or d.get("lifting_station")
        ]
        for node in stations:
            head = G.nodes[node].get("total_static_head")
            assert head is not None, f"station {node} lacks total_static_head"
            assert head >= 0
