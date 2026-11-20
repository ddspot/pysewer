# SPDX-FileCopyrightText: 2025 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for pysewer.viz.mpl (behaviour, not pixels)."""

import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("matplotlib")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection

import pysewer
from pysewer.bundle import NetworkBundle
from pysewer.viz import longitudinal_profile
from pysewer.viz.mpl import PALETTE, plot_network, plot_profile

from .test_bundle import _synthetic

TEST_DATA = Path("tests") / "test_data"
SINK = (691350, 2553250)


@pytest.fixture(autouse=True)
def _close():
    yield
    plt.close("all")


@pytest.fixture
def G():
    return _synthetic()


@pytest.fixture
def b(G):
    bb = NetworkBundle.from_graph(G, name="syn")
    # signed difference column; pipes are P000001..P000004 -> [-2, -1, 0, 3]
    bb.pipes["delta_depth"] = [-2.0, -1.0, 0.0, 3.0]
    return bb


def _legend_labels(ax):
    leg = ax.get_legend()
    return [t.get_text() for t in leg.get_texts()] if leg else []


def _lines(ax):
    """The pipe line collection (the one that is not a halo, zorder 3)."""
    lcs = [c for c in ax.collections if isinstance(c, LineCollection)]
    return [c for c in lcs if c.get_zorder() == 3]


# ------------------------------------------------------------------ plot_network
def test_returns_fig_ax(b):
    fig, ax = plot_network(b)
    assert fig is ax.figure
    assert isinstance(fig, plt.Figure)
    assert ax.get_title() == "syn"


def test_title_override_and_ax_passed(b):
    fig0, ax0 = plt.subplots()
    fig, ax = plot_network(b, ax=ax0, title="hello")
    assert fig is fig0 and ax is ax0
    assert ax.get_title() == "hello"


def test_accepts_graph(G):
    _fig, ax = plot_network(G)
    assert len(_lines(ax)) == 1
    # 4 pipes, one geometry part each
    assert len(_lines(ax)[0].get_segments()) == 4


@pytest.mark.parametrize("col", ["diameter", "pipe_type"])
def test_discrete_options_have_no_colorbar(b, col):
    fig, _ax = plot_network(b, color_by=col)
    assert len(fig.axes) == 1


@pytest.mark.parametrize(
    "col", ["depth", "velocity", "d_over_D", "peak_flow", "delta_depth"]
)
def test_continuous_options_have_colorbar(b, col):
    fig, ax = plot_network(b, color_by=col)
    assert len(fig.axes) == 2  # map + colorbar
    cb = fig.axes[1]
    assert cb is not ax


def test_peak_flow_colorbar_in_litres_per_s(b):
    # peak flows 0.003, 0.001, 0.002, NaN(K->S has none) -> 1..3 L/s
    fig, _ax = plot_network(b, color_by="peak_flow")
    cb = fig.axes[1]
    assert "L/s" in cb.get_xlabel()
    assert cb.get_xlim() == pytest.approx((1.0, 3.0), abs=1e-9)


def test_delta_colorbar_symmetric_about_zero(b):
    # max |delta| = 3 -> [-3, 3]
    fig, _ax = plot_network(b, color_by="delta_depth")
    assert fig.axes[1].get_xlim() == pytest.approx((-3.0, 3.0), abs=1e-9)


def test_unknown_color_by_raises_value_error(b):
    with pytest.raises(ValueError, match="not_a_column"):
        plot_network(b, color_by="not_a_column")


def test_basemap_not_implemented(b):
    with pytest.raises(NotImplementedError):
        plot_network(b, basemap="osm")


def test_pressurized_dashed(b):
    # P000003 (B->P) is the only pressurized pipe; others are gravity or
    # undesigned (K->S, no diameter)
    _fig, ax = plot_network(b, color_by="pipe_type")
    lc = _lines(ax)[0]
    styles = lc.get_linestyle()
    assert len(styles) == 4
    ptypes = list(b.pipes["pipe_type"])
    dashed = [s for s, t in zip(styles, ptypes, strict=True) if t == "pressurized"]
    solid = [s for s, t in zip(styles, ptypes, strict=True) if t == "gravity"]
    assert len(dashed) == 1 and len(solid) == 2
    # dashed has a non-empty on/off pattern, solid is (0, None)
    assert dashed[0][1] is not None and len(dashed[0][1]) >= 2
    assert solid[0][1] is None


def test_diameter_width_scaling(b):
    # diameters: 0.3, 0.2, 0.1, NaN -> widths 0.8 + 6*(d-0.1)/0.2
    # = 6.8 (0.3), 3.8 (0.2), 0.8 (0.1)
    _fig, ax = plot_network(b)
    lw = sorted(_lines(ax)[0].get_linewidth())
    assert lw[0] == pytest.approx(0.6)  # NaN diameter fallback
    assert lw[1:] == pytest.approx([0.8, 3.8, 6.8])


def test_width_by_none_constant(b):
    _fig, ax = plot_network(b, width_by=None)
    assert set(np.round(_lines(ax)[0].get_linewidth(), 6)) == {1.5}


def test_findings_halo_counts_distinct_pipes(b):
    f = pd.DataFrame(
        {
            "element_type": ["pipe", "pipe", "pipe", "node"],
            "element_id": ["P000001", "P000001", "P000002", "N000001"],
            "severity": ["warning", "error", "info", "error"],
        }
    )
    _fig, ax = plot_network(b, findings=f)
    halos = [
        c
        for c in ax.collections
        if isinstance(c, LineCollection) and c.get_zorder() == 2
    ]
    assert len(halos) == 1
    assert len(halos[0].get_segments()) == 2  # P000001, P000002 (node ignored)
    labels = _legend_labels(ax)
    assert "finding: error" in labels  # highest severity wins for P000001
    assert "finding: info" in labels
    assert "finding: warning" not in labels


def test_no_findings_no_halo(b):
    fig, ax = plot_network(b)
    assert not [
        c
        for c in ax.collections
        if isinstance(c, LineCollection) and c.get_zorder() == 2
    ]
    fig, ax = plot_network(
        b, findings=pd.DataFrame(columns=["element_type", "element_id", "severity"])
    )
    assert len(fig.axes) == 1


def test_findings_without_id_column_raises(b):
    with pytest.raises(ValueError):
        plot_network(b, findings=pd.DataFrame({"severity": ["error"]}))


def test_legend_node_types_present_only(b):
    _fig, ax = plot_network(b)
    labels = _legend_labels(ax)
    assert "sink (WWTP)" in labels
    assert "pumping station" in labels
    assert "lifting station" in labels
    assert "building" in labels
    assert "private pump" in labels  # N000001 is a private pump
    nb = NetworkBundle.from_graph(_synthetic(), name="x")
    nb.nodes["pumping_station"] = False
    nb.nodes["private_pump"] = False
    _fig, ax = plot_network(nb)
    labels = _legend_labels(ax)
    assert "pumping station" not in labels
    assert "private pump" not in labels
    assert "lifting station" in labels


def test_show_nodes_false_and_legend_false(b):
    _fig, ax = plot_network(b, show_nodes=False, legend=False)
    assert ax.get_legend() is None
    assert not [c for c in ax.collections if not isinstance(c, LineCollection)]


def test_equal_aspect_and_axis_labels(b):
    _fig, ax = plot_network(b)
    assert ax.get_aspect() == 1.0
    assert "Easting" in ax.get_xlabel()


def test_empty_bundle_does_not_crash(G):
    G2 = _synthetic()
    G2.remove_edges_from(list(G2.edges))
    try:
        eb = NetworkBundle.from_graph(G2, name="empty")
    except Exception:
        pytest.skip("bundle cannot be empty")
    fig, ax = plot_network(eb)
    assert fig is ax.figure


# ------------------------------------------------------------------ plot_profile
@pytest.fixture
def prof(b):
    return longitudinal_profile(b, "N000001")


def test_plot_profile_returns_and_legend(prof):
    fig, ax = plot_profile(prof)
    assert fig is ax.figure
    labels = _legend_labels(ax)
    assert "ground" in labels
    assert "pressurized" in labels  # B->P is pressurized
    assert "Sink" in labels
    assert ax.get_title(loc="left") == "Longitudinal section"
    assert ax.get_ylabel().startswith("Elevation")


def test_plot_profile_title_and_ax(prof):
    fig0, ax0 = plt.subplots(figsize=(10, 6))
    fig, ax = plot_profile(prof, ax=ax0, title="T")
    assert ax is ax0 and fig is fig0
    assert ax.get_title(loc="left") == "T"


def test_plot_profile_xlim_covers_total_length(prof):
    _fig, ax = plot_profile(prof)
    # 1 % padding each side of [0, total]
    lo, hi = ax.get_xlim()
    assert lo == pytest.approx(-0.01 * prof.total_length)
    assert hi == pytest.approx(1.01 * prof.total_length)


def test_vertical_exaggeration_sets_aspect(prof):
    _fig, ax = plot_profile(prof, vertical_exaggeration=20)
    assert ax.get_aspect() == 20.0
    texts = [t.get_text() for t in ax.texts]
    assert any("20x" in t for t in texts)


def test_no_vertical_exaggeration_keeps_auto_aspect(prof):
    _fig, ax = plot_profile(prof)
    assert ax.get_aspect() == "auto"


def test_show_labels_false_has_no_band(prof):
    _fig, ax = plot_profile(prof, show_labels=False)
    assert ax.child_axes == []
    assert not any("Sink" in t.get_text() for t in ax.texts)
    assert ax.get_xlabel() == "Chainage [m]"


def test_show_labels_true_has_band(prof):
    _fig, ax = plot_profile(prof, show_labels=True)
    assert len(ax.child_axes) == 1
    band_texts = [t.get_text() for t in ax.child_axes[0].texts]
    assert any("pressurized" in t for t in band_texts)
    assert any(t.startswith("DN") for t in band_texts)
    assert any("Sink" in t.get_text() for t in ax.texts)


def test_show_water_toggle(prof):
    _, ax = plot_profile(prof, show_water=True)
    assert "water level" in _legend_labels(ax)
    _, ax = plot_profile(prof, show_water=False)
    assert "water level" not in _legend_labels(ax)


def test_lifting_station_and_drop_markers_in_legend(prof):
    # markers on the path N1 -> N4: start, lifting, pumping, sink
    kinds = {m.kind for m in prof.markers}
    assert {"lifting_station", "pumping_station", "sink"} <= kinds
    _, ax = plot_profile(prof)
    labels = _legend_labels(ax)
    assert "Lifting station" in labels
    assert "Pumping station" in labels
    assert ("drop manhole" in labels) == ("drop" in kinds)
    assert "pump relay (no lift on this path)" not in labels


def test_start_at_sink_does_not_crash(b):
    p = longitudinal_profile(b, "N000006")
    fig, ax = plot_profile(p)
    assert fig is ax.figure
    fig, ax = plot_profile(p, show_labels=False)
    assert fig is ax.figure


def test_single_pipe_profile(b):
    p = longitudinal_profile(b, "N000003")  # P -> S, one gravity pipe
    _fig, ax = plot_profile(p)
    assert "pressurized" not in _legend_labels(ax)
    # 140 m long pipe, slope (104 - 102)/140 = 14.29 permille? invert slope
    # is shown in the label band as "<x>‰"
    band_texts = [t.get_text() for t in ax.child_axes[0].texts]
    assert any("‰" in t for t in band_texts)


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


def test_real_plot_network_all_options(real):
    n = len(real.pipes)
    for col in ("diameter", "pipe_type", "depth", "velocity", "d_over_D", "peak_flow"):
        fig, ax = plot_network(real, color_by=col)
        segs = sum(len(c.get_segments()) for c in _lines(ax))
        assert segs >= n  # one per geometry part
        plt.close(fig)


def test_real_profile_relay_and_pumping_station(real):
    nodes = set(real.nodes["node_id"])
    if "N000018" not in nodes or "N000115" not in nodes:
        pytest.skip("node ids changed")
    p = longitudinal_profile(real, "N000115")
    assert any(m.kind == "pump_relay" for m in p.markers)
    _, ax = plot_profile(p)
    labels = _legend_labels(ax)
    assert "pump relay (no lift on this path)" in labels
    # a genuine pumping station on the path is legended iff a marker has it
    assert ("Pumping station" in labels) == any(
        m.kind == "pumping_station" for m in p.markers
    )

    q = longitudinal_profile(real, "N000023")
    assert any(m.kind == "pumping_station" for m in q.markers)
    _, ax = plot_profile(q)
    assert "Pumping station" in _legend_labels(ax)
    # N000018 is a genuine pumping station here, not a relay
    kq = {m.node_id: m.kind for m in q.markers}
    assert kq["N000018"] == "pumping_station"


# ------------------------------------------------------------------ import
def test_light_import_viz_then_mpl():
    code = (
        "import sys, pysewer, pysewer.viz\n"
        "assert 'matplotlib' not in sys.modules\n"
        "assert 'osmnx' not in sys.modules\n"
        "import pysewer.viz.mpl\n"
        "assert 'matplotlib' in sys.modules\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_palette_has_roles():
    for k in ("gravity", "pressurized", "sink", "lifting_station", "pumping_station"):
        assert PALETTE[k].startswith("#") and len(PALETTE[k]) == 7
