# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""plot_model_domain deprecates the junction graph; plot_connection_graph marks pump nodes."""

import shutil
import warnings
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import pysewer  # noqa: E402
from pysewer.plotting import plot_connection_graph, plot_model_domain  # noqa: E402

TEST_DATA = Path("tests") / "test_data"
SINK = (691350, 2553250)


@pytest.fixture
def md(tmp_path):
    shutil.copy(TEST_DATA / "dem.tif", tmp_path / "dem.tif")
    m = pysewer.ModelDomain(
        tmp_path / "dem.tif",
        TEST_DATA / "roads_clipped.shp",
        TEST_DATA / "buildings_clipped.shp",
    )
    m.add_sink(SINK)
    m.connection_graph = m.generate_connection_graph()
    for _, _, d in m.connection_graph.edges(data=True):
        d.pop("needs_pump", None)
    yield m
    plt.close("all")


def _labels(ax):
    return ax.get_legend_handles_labels()[1]


def _pump_collections(ax):
    return [c for c in ax.collections if c.get_label() == "Pump Needed"]


def _dep(record):
    return [w for w in record if issubclass(w.category, DeprecationWarning)
            and "junction graph" in str(w.message)]


def test_junction_graph_emits_one_deprecation_warning(md):
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        plot_model_domain(md, plot_junction_graph=True, plot_connection_graph=False,
                          hillshade=False, fig_size=(4, 4))
    assert len(_dep(rec)) == 1


def test_junction_graph_plots_connection_graph_once(md):
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        _, ax = plot_model_domain(md, plot_junction_graph=True, hillshade=False,
                                  fig_size=(4, 4))
    assert len(_dep(rec)) == 1
    assert _labels(ax).count("Connection Graph") == 1


def test_both_flags_draw_connection_graph_once(md):
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        _, ax = plot_model_domain(md, plot_junction_graph=True,
                                  plot_connection_graph=True, hillshade=False,
                                  fig_size=(4, 4))
    assert len(_dep(rec)) == 1
    assert _labels(ax).count("Connection Graph") == 1


def test_no_junction_flag_no_warning(md):
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        _, ax = plot_model_domain(md, plot_junction_graph=False,
                                  plot_connection_graph=True, hillshade=False,
                                  fig_size=(4, 4))
    assert _dep(rec) == []
    assert _labels(ax).count("Connection Graph") == 1


def _flag(G, a, b, k, value=True):
    G.edges[a, b, k]["needs_pump"] = value


def test_pump_nodes_highlighted_and_deduped(md):
    G = md.connection_graph
    assert G.is_multigraph() and G.is_directed()
    u = next(n for n in G.nodes if G.out_degree(n) >= 2)
    e1, e2 = list(G.out_edges(u, keys=True))[:2]
    # a third edge with a different upstream node
    e3 = next(e for e in G.edges(keys=True) if e[0] != u)
    for a, b, k in (e1, e2, e3):
        _flag(G, a, b, k)
    expected = {u, e3[0]}  # 3 flagged edges, 2 unique upstream nodes
    assert len(expected) == 2

    _, ax = plot_connection_graph(md, fig_size=(4, 4))
    assert _labels(ax).count("Pump Needed") == 1
    (coll,) = _pump_collections(ax)
    assert len(coll.get_offsets()) == 2
    offsets = {tuple(map(float, o)) for o in coll.get_offsets()}
    assert offsets == {(float(x), float(y)) for x, y in expected}


def test_pump_gdf_has_graph_crs(md, monkeypatch):
    import geopandas as gpd

    G = md.connection_graph
    a, b, k = next(iter(G.edges(keys=True)))
    _flag(G, a, b, k)
    import types

    import pysewer.plotting as plotting

    seen = []

    class Recording(gpd.GeoDataFrame):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            seen.append(self)

    monkeypatch.setattr(
        plotting, "gpd", types.SimpleNamespace(GeoDataFrame=Recording)
    )
    plot_connection_graph(md, fig_size=(4, 4))
    assert len(seen) == 1
    assert isinstance(seen[0], gpd.GeoDataFrame)
    assert len(seen[0]) == 1
    assert seen[0].crs == G.graph["crs"]
    assert (seen[0].geometry.iloc[0].x, seen[0].geometry.iloc[0].y) == (a[0], a[1])


def test_falsy_needs_pump_no_pump_layer(md):
    G = md.connection_graph
    for a, b, k in list(G.edges(keys=True))[:3]:
        _flag(G, a, b, k, False)
    _, ax = plot_connection_graph(md, fig_size=(4, 4))
    assert "Pump Needed" not in _labels(ax)
    assert _pump_collections(ax) == []


def test_no_needs_pump_attribute_no_pump_layer(md):
    _, ax = plot_connection_graph(md, fig_size=(4, 4))
    assert "Pump Needed" not in _labels(ax)
    assert "Connections" in _labels(ax)
