# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""
Static matplotlib figures for a designed sewer network.

* :func:`plot_network` draws the plan view of a
  :class:`~pysewer.bundle.NetworkBundle`, coloured by diameter, pipe type or a
  numeric column.
* :func:`plot_profile` draws an engineering longitudinal section (chainage on
  x, elevation on y): ground line, pipe invert and crown, manholes, stations
  and a band with the pipe labels.

This module needs matplotlib (the ``plot`` extra). ``pysewer.viz`` does not
import it by default; import it explicitly with ``from pysewer.viz import mpl``.

All colours come from :data:`PALETTE`. They are the colour-blind-safe
Paul Tol "bright" and "muted" colours; sequential scales use Blues and
viridis, and a diverging scale is used only for signed differences. The sink,
station and pressurized colours and the Blues diameter ramp are the same as in
``benchmarks/scripts/plot_comparisons.py``.
"""

from __future__ import annotations

import math
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import cm
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize, TwoSlopeNorm
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

__all__ = ["PALETTE", "plot_network", "plot_profile"]

#: One palette for every figure. Pipe-type and role colours are colour-blind
#: safe (Tol bright/muted) and shared with the benchmark figures.
PALETTE: dict[str, str] = {
    "gravity": "#4477AA",
    "pressurized": "#EE7733",
    "undesigned": "#9A9A9A",
    "sink": "#CC3311",
    "pumping_station": "#EE6677",
    "lifting_station": "#228833",
    "private_pump": "#AA3377",
    "building": "#8A8A8A",
    "ground": "#8C6D46",
    "ground_fill": "#E6D9C3",
    "pipe_fill": "#BBCCEE",
    "water": "#77AADD",
    "manhole": "#555555",
    "drop": "#CCBB44",
    "error": "#CC3311",
    "warning": "#EEAA22",
    "info": "#66CCEE",
}

_DIAMETER_CMAP = "Blues"
_CONTINUOUS_CMAP = "viridis"
_DIVERGING_CMAP = "PuOr"
_DASHED = (0, (3, 1.6))
_DOTTED = (0, (1, 1.8))
_GRID_COLOUR = "#E0E0E0"
_TEXT_COLOUR = "#444444"
_BAND_TEXT_COLOUR = "#222222"
_BAND_LINE_COLOUR = "#999999"
_FONT = 8  # axis, legend and label font size [pt]
_FONT_BAND_SMALL = 7
_FONT_BAND_TINY = 6

# plan view
_WIDTH_UNKNOWN = 0.6  # line width of pipes without a diameter [pt]
_WIDTH_MIN = 0.8
_WIDTH_RANGE = 6.0
_WIDTH_SINGLE = 2.0  # added to _WIDTH_MIN when all diameters are equal
_WIDTH_CONSTANT = 1.5
_HALO_EXTRA_WIDTH = 3.0
_HALO_ALPHA = 0.45
_RAMP_START, _RAMP_SPAN = 0.35, 0.6  # used part of the Blues ramp
_MAP_MARGIN = 0.04

# longitudinal section
_LABEL_RIGHT_FRAC = 0.88  # a marker label right of this fraction is right-aligned
_LABEL_LEFT_FRAC = 0.12  # and left of this fraction left-aligned
_BAND_NARROW_FRAC = 0.015  # segments narrower than this get no label
_BAND_VERTICAL_FRAC = 0.07  # segments narrower than this get a rotated label
_LABEL_OFFSET_PT = 16  # marker label offset above the ground [pt]
_LABEL_STAGGER_PT = 12  # extra offset of every second marker label [pt]
_BAND_BOTTOM, _BAND_HEIGHT = -0.27, 0.20  # label band in axes fractions
_FIG_SIZE_PROFILE = (11, 5.2)
_FIG_BOTTOM_LABELS, _FIG_BOTTOM_PLAIN = 0.36, 0.12
_FIG_MARGINS = {"left": 0.07, "right": 0.98, "top": 0.86}
_LEGEND_COL_WIDTH_IN = 1.5
_TITLE_PAD, _TITLE_ROW_PAD = 6, 14
_PAD_MIN, _PAD_FRAC = 0.5, 0.12  # vertical padding of the elevation range
_LABEL_HEADROOM = 2.2  # extra headroom for the labels, in paddings
_VE_INTEGER_FROM = 2
_STATION_MARKER = {"lifting_station": "D", "pumping_station": "^"}

# column -> (label, unit, scale factor applied to the values)
_QUANTITIES: dict[str, tuple[str, str, float]] = {
    "depth": ("Max. end depth", "m", 1.0),
    "velocity": ("Velocity", "m/s", 1.0),
    "d_over_D": ("Filling ratio d/D", "-", 1.0),
    "peak_flow": ("Peak flow", "L/s", 1000.0),
    "diameter": ("Diameter", "mm", 1000.0),
    "length": ("Length", "m", 1.0),
    "invert_slope": ("Invert slope", "m/m", 1.0),
    "ground_slope": ("Ground slope", "m/m", 1.0),
}
_SEVERITY_RANK = {"error": 3, "warning": 2, "info": 1}


# --------------------------------------------------------------------------
# Plan view
# --------------------------------------------------------------------------
def _as_bundle(obj: Any):
    from pysewer.bundle import NetworkBundle

    if isinstance(obj, NetworkBundle):
        return obj
    return NetworkBundle.from_graph(obj)


def _line_coords(geom) -> list[np.ndarray]:
    if geom is None or geom.is_empty:
        return []
    parts = geom.geoms if hasattr(geom, "geoms") else [geom]
    return [np.asarray(p.coords)[:, :2] for p in parts]


def _width(diameter: pd.Series) -> np.ndarray:
    d = diameter.to_numpy(float)
    ok = np.isfinite(d)
    out = np.full(len(d), _WIDTH_UNKNOWN)
    if ok.any():
        lo, hi = d[ok].min(), d[ok].max()
        span = hi - lo
        scaled = _WIDTH_RANGE * (d[ok] - lo) / span if span > 0 else _WIDTH_SINGLE
        out[ok] = _WIDTH_MIN + scaled
    return out


def _pipe_values(pipes: pd.DataFrame, column: str) -> pd.Series:
    if column == "depth":
        return pipes[["depth_up", "depth_down"]].max(axis=1)
    if column not in pipes.columns:
        raise ValueError(
            f"color_by={column!r} is not a pipe column; use 'diameter', "
            f"'pipe_type', 'depth' or one of {sorted(pipes.columns)}"
        )
    return pd.to_numeric(pipes[column], errors="coerce")


def _is_delta(name: str) -> bool:
    n = name.lower()
    return n.startswith(("delta", "diff")) or n.endswith(("_delta", "_diff"))


def _halo_colors(findings: pd.DataFrame | None, pipes: pd.DataFrame) -> dict:
    if findings is None or len(findings) == 0:
        return {}
    f = findings
    if "element_type" in f.columns:
        f = f[f["element_type"] == "pipe"]
    if "element_id" in f.columns:
        ids = f["element_id"]
    elif "pipe_id" in f.columns:
        ids = f["pipe_id"]
    else:
        raise ValueError("findings needs an 'element_id' or 'pipe_id' column")
    sev = (
        f["severity"]
        if "severity" in f.columns
        else pd.Series("warning", index=f.index)
    )
    best: dict[str, str] = {}
    for pid, s in zip(ids, sev, strict=False):
        s = str(s)
        if _SEVERITY_RANK.get(s, 0) > _SEVERITY_RANK.get(best.get(pid, ""), 0):
            best[pid] = s
    return best


def _plan_aspect(ax, geographic: bool) -> None:
    """Equal aspect; for lon/lat degrees correct by 1 / cos(mean latitude)."""
    if geographic:
        lat = float(np.mean(ax.get_ylim()))
        ax.set_aspect(1.0 / max(math.cos(math.radians(lat)), 1e-6), adjustable="box")
    else:
        ax.set_aspect("equal", adjustable="box")


def _plan_segments(pipes: pd.DataFrame) -> tuple[list[np.ndarray], np.ndarray]:
    """Line parts of all pipes and, per part, the row index of its pipe."""
    segs: list[np.ndarray] = []
    rows: list[int] = []
    for i, geom in enumerate(pipes.geometry):
        parts = _line_coords(geom)
        segs.extend(parts)
        rows.extend([i] * len(parts))
    return segs, np.asarray(rows, int)


def _line_styles(ptype: np.ndarray) -> np.ndarray:
    styles = {"pressurized": _DASHED, "undesigned": _DOTTED}
    out = np.empty(len(ptype), object)
    for i, t in enumerate(ptype):
        out[i] = styles.get(t, "solid")
    return out


def _colours_by_type(ptype: np.ndarray):
    cols = {t: PALETTE[t] for t in ("gravity", "pressurized", "undesigned")}
    handles = [
        Line2D([], [], color=cols[t], ls=ls, lw=2.2, label=t)
        for t, ls in (
            ("gravity", "solid"),
            ("pressurized", _DASHED),
            ("undesigned", _DOTTED),
        )
        if (ptype == t).any()
    ]
    return [cols.get(t, PALETTE["undesigned"]) for t in ptype], handles


def _colours_by_diameter(pipes: pd.DataFrame, ptype: np.ndarray, lw: np.ndarray):
    grey = PALETTE["undesigned"]
    d = pipes["diameter"].to_numpy(float)
    gravity = ptype == "gravity"
    classes = np.unique(np.round(d[gravity & np.isfinite(d)], 3))
    ramp = plt.get_cmap(_DIAMETER_CMAP)
    class_col = {
        c: ramp(_RAMP_START + _RAMP_SPAN * i / max(1, len(classes) - 1))
        for i, c in enumerate(classes)
    }
    cols = []
    for t, di in zip(ptype, d, strict=True):
        if t == "pressurized":
            cols.append(PALETTE["pressurized"])
        elif t == "gravity" and np.isfinite(di):
            cols.append(class_col[round(di, 3)])
        else:
            cols.append(grey)
    handles = []
    for c in classes:
        sel = gravity & (np.round(d, 3) == c)
        handles.append(
            Line2D(
                [],
                [],
                color=class_col[c],
                lw=float(np.median(lw[sel])),
                label=f"gravity DN{c * 1000:.0f}",
            )
        )
    if (ptype == "pressurized").any():
        handles.append(
            Line2D(
                [],
                [],
                color=PALETTE["pressurized"],
                ls=_DASHED,
                lw=2.0,
                label="pressurized",
            )
        )
    if (ptype == "undesigned").any():
        handles.append(
            Line2D([], [], color=grey, ls=_DOTTED, lw=1.5, label="undesigned")
        )
    return cols, handles


def _colours_by_value(pipes: pd.DataFrame, color_by: str):
    grey = PALETTE["undesigned"]
    label, unit, scale = _QUANTITIES.get(color_by, (color_by, "", 1.0))
    v = _pipe_values(pipes, color_by).to_numpy(float) * scale
    ok = np.isfinite(v)
    if _is_delta(color_by):
        m = float(np.abs(v[ok]).max()) if ok.any() else 1.0
        m = m or 1.0
        norm = TwoSlopeNorm(0.0, -m, m)
        cmap = plt.get_cmap(_DIVERGING_CMAP)
    else:
        lo, hi = (float(v[ok].min()), float(v[ok].max())) if ok.any() else (0.0, 1.0)
        if color_by == "d_over_D":
            lo, hi = 0.0, max(1.0, hi)
        if hi <= lo:
            hi = lo + 1.0
        norm = Normalize(lo, hi)
        cmap = plt.get_cmap(_CONTINUOUS_CMAP)
    cols = [cmap(norm(x)) if np.isfinite(x) else grey for x in v]
    handles = []
    if (~ok).any():
        handles.append(Line2D([], [], color=grey, lw=1.5, label="no value"))
    bar_label = f"{label} [{unit}]" if unit and unit != "-" else label
    return cols, handles, cm.ScalarMappable(norm, cmap), bar_label


def _pipe_colours(pipes: pd.DataFrame, color_by: str, lw: np.ndarray):
    """Colour per pipe row, legend handles, colour-bar mappable and label."""
    ptype = pipes["pipe_type"].to_numpy(object)
    if color_by == "pipe_type":
        cols, handles = _colours_by_type(ptype)
        return cols, handles, None, None
    if color_by == "diameter":
        cols, handles = _colours_by_diameter(pipes, ptype, lw)
        return cols, handles, None, None
    return _colours_by_value(pipes, color_by)


def _draw_halo(ax, pipes, findings, segs, rows, lw) -> list:
    """Draw halos under flagged pipes; return their legend handles."""
    halo = _halo_colors(findings, pipes)
    if not halo:
        return []
    halo_cols = {s: PALETTE[s] for s in ("error", "warning", "info")}
    ids = pipes["pipe_id"].to_numpy()
    flagged = np.array([pid in halo for pid in ids], bool)
    sel = flagged[rows] if len(rows) else np.zeros(0, bool)
    picked = [(s, r) for s, r, k in zip(segs, rows, sel, strict=True) if k]
    ax.add_collection(
        LineCollection(
            [s for s, _ in picked],
            colors=[halo_cols.get(halo[ids[r]], PALETTE["warning"]) for _, r in picked],
            linewidths=[lw[r] + _HALO_EXTRA_WIDTH for _, r in picked],
            alpha=_HALO_ALPHA,
            capstyle="round",
            zorder=2,
        )
    )
    present = {halo[pid] for pid in ids[flagged]}
    return [
        Line2D(
            [], [], color=halo_cols[s], lw=6, alpha=_HALO_ALPHA, label=f"finding: {s}"
        )
        for s in ("error", "warning", "info")
        if s in present
    ]


def _node_flag(nodes: pd.DataFrame, column: str) -> np.ndarray:
    if column in nodes:
        return nodes[column].fillna(False).astype(bool).to_numpy()
    return np.zeros(len(nodes), bool)


def _legend_markersize(label: str, kw: dict) -> float:
    if label == "building":
        return 4
    return 13 if kw["marker"] == "*" else math.sqrt(kw["s"])


def _draw_nodes(ax, nodes: pd.DataFrame) -> list:
    """Scatter sink, stations, private pumps and buildings; return handles."""
    role = nodes["role"].to_numpy(object)
    priv = _node_flag(nodes, "private_pump")
    white = {"edgecolors": "white"}
    layers = [
        (
            (role == "building") & ~priv,
            dict(marker="o", s=3, c=PALETTE["building"], lw=0, zorder=4),
            "building",
        ),
        (
            priv,
            dict(
                marker="o",
                s=14,
                c=PALETTE["private_pump"],
                linewidths=0.3,
                zorder=5,
                **white,
            ),
            "private pump",
        ),
        (
            _node_flag(nodes, "lifting_station"),
            dict(
                marker="D",
                s=46,
                c=PALETTE["lifting_station"],
                linewidths=0.6,
                zorder=7,
                **white,
            ),
            "lifting station",
        ),
        (
            _node_flag(nodes, "pumping_station"),
            dict(
                marker="^",
                s=70,
                c=PALETTE["pumping_station"],
                linewidths=0.6,
                zorder=8,
                **white,
            ),
            "pumping station",
        ),
        (
            role == "sink",
            dict(
                marker="*", s=300, c=PALETTE["sink"], linewidths=0.8, zorder=9, **white
            ),
            "sink (WWTP)",
        ),
    ]
    xs, ys = nodes.geometry.x.to_numpy(), nodes.geometry.y.to_numpy()
    handles = []
    for mask, kw, label in layers:
        if mask.any():
            ax.scatter(xs[mask], ys[mask], **kw)
            handles.append(
                Line2D(
                    [],
                    [],
                    ls="",
                    marker=kw["marker"],
                    color=kw["c"],
                    markersize=_legend_markersize(label, kw),
                    markeredgecolor="white",
                    markeredgewidth=0.5,
                    label=label,
                )
            )
    return handles


def plot_network(
    bundle,
    color_by: str = "diameter",
    ax=None,
    *,
    width_by: str | None = "diameter",
    show_nodes: bool = True,
    basemap: Any = None,
    title: str | None = None,
    legend: bool = True,
    findings: pd.DataFrame | None = None,
):
    """
    Plan view of a designed network in its projected CRS.

    Parameters
    ----------
    bundle : NetworkBundle or networkx.DiGraph
        A bundle or a designed graph (converted with ``from_graph``).
    color_by : str, default "diameter"
        ``"diameter"`` (discrete classes, Blues ramp; pressurized pipes use
        their own colour), ``"pipe_type"`` (gravity solid, pressurized dashed,
        undesigned dotted grey), ``"depth"`` (maximum of the two end depths,
        m), or any numeric pipe column such as ``"velocity"``, ``"d_over_D"``
        or ``"peak_flow"`` (shown in L/s). Numeric columns get a viridis
        colour bar; columns named ``delta*``/``diff*`` get a diverging scale
        centred on zero. Pipes without a value are grey.
    ax : matplotlib.axes.Axes, optional
        Axes to draw on. A new pyplot figure is made when None.
    width_by : {"diameter", None}, default "diameter"
        Scale the line width with the diameter (0.8 to 6.8 pt). None gives a
        constant 1.5 pt.
    show_nodes : bool, default True
        Draw sink, pumping and lifting stations, private pumps and buildings.
    basemap : None
        Reserved. No basemap is downloaded; only None is accepted.
    title : str, optional
        Axes title.
    legend : bool, default True
        Draw the legend (outside the axes, right).
    findings : pandas.DataFrame, optional
        Output of ``pysewer.metrics.quality_checks``. Flagged pipes get a halo
        (red error, amber warning, cyan info; the highest severity wins).

    Returns
    -------
    fig, ax : matplotlib.figure.Figure, matplotlib.axes.Axes

    Notes
    -----
    When ``ax`` is None, a new pyplot figure is created and stays open: the
    caller must close it (``plt.close(fig)``). For a geographic CRS (WGS84
    bundle) the aspect is ``1 / cos(mean latitude)``, so the map is not
    distorted; for a projected CRS it is equal.
    """
    if basemap is not None:
        raise NotImplementedError("basemap is reserved; only basemap=None works")
    b = _as_bundle(bundle)
    pipes, nodes = b.pipes, b.nodes
    geographic = bool(b.crs.is_geographic)

    if ax is None:
        fig, ax = plt.subplots(figsize=(9, 7), layout="constrained")
    else:
        fig = ax.figure

    segs, rows = _plan_segments(pipes)
    ptype = pipes["pipe_type"].to_numpy(object)
    if width_by == "diameter":
        lw = _width(pipes["diameter"])
    else:
        lw = np.full(len(pipes), _WIDTH_CONSTANT)
    ls_row = _line_styles(ptype)
    col_row, handles, mappable, bar_label = _pipe_colours(pipes, color_by, lw)

    handles += _draw_halo(ax, pipes, findings, segs, rows, lw)
    ax.add_collection(
        LineCollection(
            segs,
            colors=[col_row[r] for r in rows],
            linewidths=lw[rows],
            linestyles=[ls_row[r] for r in rows],
            capstyle="butt",
            zorder=3,
        )
    )
    if show_nodes and len(nodes):
        handles += _draw_nodes(ax, nodes)

    ax.autoscale_view()
    ax.margins(_MAP_MARGIN)
    _plan_aspect(ax, geographic)
    ax.ticklabel_format(useOffset=False, style="plain")
    ax.tick_params(labelsize=_FONT)
    ax.set_xlabel("Longitude [deg]" if geographic else "Easting [m]")
    ax.set_ylabel("Latitude [deg]" if geographic else "Northing [m]")
    ax.set_title(title if title is not None else b.name)
    ax.grid(color=_GRID_COLOUR, lw=0.4, zorder=0)
    ax.set_axisbelow(True)

    if mappable is not None:
        cb = fig.colorbar(
            mappable, ax=ax, location="bottom", shrink=0.6, pad=0.08, aspect=35
        )
        cb.set_label(bar_label)
    if legend and handles:
        ax.legend(
            handles=handles,
            loc="upper left",
            bbox_to_anchor=(1.01, 1.0),
            fontsize=_FONT,
            frameon=False,
            borderaxespad=0.0,
            title="Legend",
            title_fontsize=_FONT,
            alignment="left",
        )
    return fig, ax


# --------------------------------------------------------------------------
# Longitudinal section
# --------------------------------------------------------------------------
def _runs(chainage: np.ndarray) -> list[slice]:
    """Index slices of the finite runs of an array with NaN separators."""
    ok = np.isfinite(chainage)
    out, start = [], None
    for i, f in enumerate(ok):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append(slice(start, i))
            start = None
    if start is not None:
        out.append(slice(start, len(ok)))
    return out


def _slope_permille(ch: np.ndarray, inv: np.ndarray) -> float:
    ok = np.isfinite(ch) & np.isfinite(inv)
    if ok.sum() < 2:
        return float("nan")
    c, z = ch[ok], inv[ok]
    span = c[-1] - c[0]
    return float((z[0] - z[-1]) / span * 1000.0) if span > 0 else float("nan")


def _nanmin(*vals) -> float:
    v = [x for x in vals if x is not None and np.isfinite(x)]
    return min(v) if v else float("nan")


def _finite(x) -> bool:
    return x is not None and bool(np.isfinite(x))


def _label_colour(kind: str) -> str:
    return "pumping_station" if kind == "pump_relay" else kind


_MARKER_LABELS = {
    "lifting_station": "Lifting station",
    "pumping_station": "Pumping station",
    "pump_relay": "Pump relay",
    "sink": "Sink",
}


def _match_segments(segments, runs, ch) -> dict[int, slice | None]:
    """Match every segment to the run closest to its chainage range."""
    seg_run: dict[int, slice | None] = {}
    for si, seg in enumerate(segments):
        best, best_d = None, float("inf")
        for r in runs:
            d = abs(ch[r][0] - seg.chainage_start) + abs(ch[r][-1] - seg.chainage_end)
            if d < best_d:
                best, best_d = r, d
        seg_run[si] = best
    return seg_run


def _draw_ground_and_pipes(ax, runs, pressurized, arrays, ybot, show_water) -> None:
    """Ground line with earth fill, then pipe fill, crown, invert and water."""
    ch, ground, invert, crown, water = arrays
    gx: list[float] = []
    gy: list[float] = []
    for k, r in enumerate(runs):
        if k and ch[r][0] - ch[runs[k - 1]][-1] > 1e-3:
            gx.append(np.nan)
            gy.append(np.nan)
        gx.extend(ch[r])
        gy.extend(ground[r])
    gx, gy = np.asarray(gx), np.asarray(gy)
    ax.fill_between(gx, gy, ybot, color=PALETTE["ground_fill"], lw=0, zorder=1)
    ax.plot(gx, gy, color=PALETTE["ground"], lw=1.8, zorder=6)

    for r in runs:
        press = (r.start, r.stop) in pressurized
        col = PALETTE["pressurized" if press else "gravity"]
        ls = _DASHED if press else "-"
        x = ch[r]
        ax.fill_between(
            x,
            invert[r],
            crown[r],
            color=PALETTE["pipe_fill"],
            alpha=0.75,
            lw=0,
            zorder=2,
            hatch="//" if press else None,
            edgecolor=col,
        )
        if show_water and not press and np.isfinite(water[r]).any():
            ax.fill_between(
                x, invert[r], water[r], color=PALETTE["water"], lw=0, zorder=3
            )
            ax.plot(x, water[r], color=PALETTE["water"], lw=0.8, zorder=3)
        ax.plot(x, crown[r], color=col, lw=0.9, ls=ls, zorder=4)
        ax.plot(x, invert[r], color=col, lw=1.8, ls=ls, zorder=5)


def _draw_station_marker(ax, kind, x, top, m) -> None:
    a, b = m.invert_in, m.invert_out
    if _finite(a) and _finite(b):
        ax.plot(
            [x, x], [a, b], color=PALETTE[kind], lw=4.0, solid_capstyle="butt", zorder=7
        )
    if np.isfinite(top):
        ax.scatter(
            [x],
            [top],
            marker=_STATION_MARKER[kind],
            s=60,
            c=PALETTE[kind],
            edgecolors="white",
            linewidths=0.7,
            zorder=9,
        )


def _draw_kind_marker(ax, kind, x, lo, top, m) -> None:
    """Draw the symbol of one marker kind."""
    if kind in _STATION_MARKER:
        _draw_station_marker(ax, kind, x, top, m)
    elif kind == "pump_relay" and np.isfinite(top):
        # pumped in and pumped on: no lift on this path, hollow marker
        ax.scatter(
            [x],
            [top],
            marker="^",
            s=60,
            facecolors="white",
            edgecolors=PALETTE["pumping_station"],
            linewidths=1.2,
            zorder=9,
        )
    elif kind == "sink" and np.isfinite(top):
        ax.scatter(
            [x],
            [top],
            marker="*",
            s=220,
            c=PALETTE["sink"],
            edgecolors="white",
            linewidths=0.7,
            zorder=9,
        )
    elif kind == "drop":
        ax.scatter(
            [x],
            [lo],
            marker="v",
            s=36,
            c=PALETTE["drop"],
            edgecolors="k",
            linewidths=0.5,
            zorder=8,
        )
    elif kind == "start" and np.isfinite(top):
        ax.scatter([x], [top], marker="o", s=14, c=PALETTE["manhole"], zorder=8)


def _label_alignment(x: float, total: float) -> str:
    if x > _LABEL_RIGHT_FRAC * total:
        return "right"
    return "left" if x < _LABEL_LEFT_FRAC * total else "center"


def _draw_markers(ax, markers, total, show_labels) -> None:
    """Manholes as vertical ticks, stations, sink and drops, with labels."""
    stagger = 0
    for m in markers:
        x, kind = float(m.chainage), m.kind
        lo = _nanmin(m.invert_in, m.invert_out)
        top = float(m.ground) if _finite(m.ground) else float("nan")
        if np.isfinite(lo) and np.isfinite(top):
            lw = 0.7 if kind in ("start", "manhole", "drop") else 0.9
            ax.plot([x, x], [lo, top], color=PALETTE["manhole"], lw=lw, zorder=4)
        _draw_kind_marker(ax, kind, x, lo, top, m)
        if show_labels and kind in _MARKER_LABELS and np.isfinite(top):
            colour = PALETTE[_label_colour(kind)]
            offset = _LABEL_OFFSET_PT + _LABEL_STAGGER_PT * (stagger % 2)
            ax.annotate(
                f"{_MARKER_LABELS[kind]} {m.node_id}",
                (x, top),
                xytext=(0, offset),
                textcoords="offset points",
                ha=_label_alignment(x, total),
                va="bottom",
                fontsize=_FONT,
                color=colour,
                arrowprops=dict(arrowstyle="-", color=colour, lw=0.7),
                zorder=10,
            )
            stagger += 1


def _profile_legend_handles(markers, has_pressurized, has_water, show_water) -> list:
    handles = [
        Line2D([], [], color=PALETTE["ground"], lw=1.8, label="ground"),
        Line2D([], [], color=PALETTE["gravity"], lw=1.8, label="invert / crown"),
    ]
    if has_pressurized:
        handles.append(
            Line2D(
                [],
                [],
                color=PALETTE["pressurized"],
                lw=1.8,
                ls=_DASHED,
                label="pressurized",
            )
        )
    if show_water and has_water:
        handles.append(Patch(color=PALETTE["water"], label="water level"))
    kinds = {m.kind for m in markers}
    for k, mk in (("lifting_station", "D"), ("pumping_station", "^"), ("sink", "*")):
        if k in kinds:
            handles.append(
                Line2D(
                    [],
                    [],
                    ls="",
                    marker=mk,
                    color=PALETTE[k],
                    markersize=12 if mk == "*" else 7,
                    markeredgecolor="white",
                    label=_MARKER_LABELS[k],
                )
            )
    if "pump_relay" in kinds:
        handles.append(
            Line2D(
                [],
                [],
                ls="",
                marker="^",
                markerfacecolor="white",
                markeredgecolor=PALETTE["pumping_station"],
                color="none",
                markersize=7,
                label="pump relay (no lift on this path)",
            )
        )
    if "drop" in kinds:
        handles.append(
            Line2D(
                [],
                [],
                ls="",
                marker="v",
                color=PALETTE["drop"],
                markeredgecolor="k",
                label="drop manhole",
            )
        )
    return handles


def _exaggeration_text(ax, vertical_exaggeration: float | None) -> str:
    """Text of the vertical exaggeration (set, or measured from the axes)."""
    if vertical_exaggeration is not None:
        return f"{float(vertical_exaggeration):g}x"
    bbox = ax.get_window_extent()
    xr, yr = ax.get_xlim(), ax.get_ylim()
    xs = bbox.width / (xr[1] - xr[0])
    ys = bbox.height / (yr[1] - yr[0])
    ve = ys / xs if xs > 0 else float("nan")
    return f"~{ve:.0f}x" if ve >= _VE_INTEGER_FROM else f"~{ve:.1f}x"


def _segment_texts(seg, run, ch, invert):
    dn = getattr(seg, "diameter", float("nan"))
    dn_txt = f"DN{dn * 1000:.0f}" if _finite(dn) else "DN?"
    if getattr(seg, "pipe_type", "gravity") == "pressurized":
        return dn_txt, "pressurized"
    sl = _slope_permille(ch[run], invert[run]) if run is not None else float("nan")
    return dn_txt, f"{sl:.1f}\u2030" if np.isfinite(sl) else "n/a"


def _label_band(ax, segments, seg_run, ch, invert) -> None:
    """Band below the axes with ``DN | slope`` per pipe segment."""
    band = ax.inset_axes([0, _BAND_BOTTOM, 1, _BAND_HEIGHT], sharex=ax)
    band.set_ylim(0, 1)
    band.set_yticks([])
    for s in ("top", "left", "right"):
        band.spines[s].set_visible(False)
    ax.tick_params(labelbottom=False)
    band.set_xlabel("Chainage [m]")
    band.tick_params(labelsize=_FONT)
    span = max(ax.get_xlim()[1] - ax.get_xlim()[0], 1e-9)
    prev_dn = None
    for si, seg in enumerate(segments):
        c0, c1 = float(seg.chainage_start), float(seg.chainage_end)
        press = getattr(seg, "pipe_type", "gravity") == "pressurized"
        band.axvline(c0, color=_BAND_LINE_COLOUR, lw=0.6)
        band.axvline(c1, color=_BAND_LINE_COLOUR, lw=0.6)
        dn_txt, slope_txt = _segment_texts(seg, seg_run.get(si), ch, invert)
        same_dn = dn_txt == prev_dn
        prev_dn = dn_txt
        frac = (c1 - c0) / span
        mid = (c0 + c1) / 2
        if frac < _BAND_NARROW_FRAC:
            continue
        if frac < _BAND_VERTICAL_FRAC:
            txt = slope_txt if same_dn else f"{dn_txt} \u00b7 {slope_txt}"
            band.text(
                mid,
                0.5,
                txt,
                rotation=90,
                ha="center",
                va="center",
                fontsize=_FONT_BAND_SMALL if same_dn else _FONT_BAND_TINY,
            )
        else:
            band.text(
                mid,
                0.5,
                f"{dn_txt}\n{slope_txt}",
                ha="center",
                va="center",
                fontsize=_FONT,
                color=PALETTE["pressurized"] if press else _BAND_TEXT_COLOUR,
            )


def plot_profile(
    profile,
    ax=None,
    *,
    vertical_exaggeration: float | None = None,
    show_water: bool = True,
    show_labels: bool = True,
    title: str | None = None,
):
    """
    Engineering longitudinal section of one flow path.

    Parameters
    ----------
    profile : pysewer.viz.profile.Profile
        Duck-typed. Needs ``to_arrays()`` (dict of ``chainage``, ``ground``,
        ``invert``, ``crown``, ``water`` arrays, NaN between pipes),
        ``markers`` (``node_id``, ``chainage``, ``role``, ``ground``,
        ``invert_in``, ``invert_out``, ``kind``), ``segments`` (``pipe_id``,
        ``pipe_type``, ``diameter``, ``chainage_start``, ``chainage_end``)
        and ``total_length``.
    ax : matplotlib.axes.Axes, optional
        Axes to draw on. A new pyplot figure is made when None. With
        ``show_labels`` a label band is added below the axes (inside the axes'
        figure margin; leave about 25 % of the figure height free).
    vertical_exaggeration : float, optional
        Ratio of the vertical to the horizontal scale. Sets the axes aspect
        (the axes box shrinks to keep it). None keeps the free aspect and
        states the exaggeration the figure has at creation.
    show_water : bool, default True
        Fill the water level between invert and ``water`` (light blue).
    show_labels : bool, default True
        Label stations and add the band with ``DN{mm} | {slope} permille`` per
        pipe. The slope is the invert slope in flow direction, positive when
        the invert falls; pressurized pipes show ``pressurized``.
    title : str, optional
        Title, default "Longitudinal section".

    Returns
    -------
    fig, ax : matplotlib.figure.Figure, matplotlib.axes.Axes

    Notes
    -----
    When ``ax`` is None, a new pyplot figure is created and stays open: the
    caller must close it (``plt.close(fig)``).

    Manholes are thin vertical lines from invert to ground; a lifting or
    pumping station draws the invert jump (``invert_in`` to ``invert_out``)
    as a thick vertical bar; drops are yellow triangles. Pressurized pipes are
    dashed and orange. Elevations are metres above sea level.
    """
    arr = {k: np.asarray(v, float) for k, v in profile.to_arrays().items()}
    ch, ground, invert = arr["chainage"], arr["ground"], arr["invert"]
    crown = arr.get("crown", np.full_like(ch, np.nan))
    water = arr.get("water", np.full_like(ch, np.nan))
    default_total = np.nanmax(ch) if np.isfinite(ch).any() else 1.0
    total = float(getattr(profile, "total_length", default_total))
    markers = list(getattr(profile, "markers", []))
    segments = list(getattr(profile, "segments", []))

    if ax is None:
        fig, ax = plt.subplots(figsize=_FIG_SIZE_PROFILE)
        bottom = _FIG_BOTTOM_LABELS if show_labels else _FIG_BOTTOM_PLAIN
        fig.subplots_adjust(bottom=bottom, **_FIG_MARGINS)
    else:
        fig = ax.figure

    runs = _runs(ch)
    seg_run = _match_segments(segments, runs, ch)
    pressurized = {
        (r.start, r.stop)
        for si, r in seg_run.items()
        if r is not None
        and getattr(segments[si], "pipe_type", "gravity") == "pressurized"
    }

    finite_all = np.concatenate([a[np.isfinite(a)] for a in (ground, invert, crown)])
    zmin, zmax = (
        (float(finite_all.min()), float(finite_all.max()))
        if finite_all.size
        else (0.0, 1.0)
    )
    pad = max(_PAD_MIN, _PAD_FRAC * (zmax - zmin))
    ybot = zmin - pad
    ytop = zmax + pad * (_LABEL_HEADROOM if show_labels else 1.0)

    _draw_ground_and_pipes(
        ax, runs, pressurized, (ch, ground, invert, crown, water), ybot, show_water
    )
    _draw_markers(ax, markers, total, show_labels)

    span = total if total > 0 else 1.0  # a profile that starts at the sink
    ax.set_xlim(-0.01 * span, total + 0.01 * span)
    ax.set_ylim(ybot, ytop)
    ax.set_ylabel("Elevation [m a.s.l.]")
    ax.grid(color=_GRID_COLOUR, lw=0.4, zorder=0)
    ax.set_axisbelow(True)

    handles = _profile_legend_handles(
        markers, bool(pressurized), np.isfinite(water).any(), show_water
    )
    if vertical_exaggeration is not None:
        ax.set_aspect(float(vertical_exaggeration), adjustable="box")
        ax.apply_aspect()
    width_in = ax.get_window_extent().width / fig.dpi
    ncol = max(2, min(len(handles), int(width_in / _LEGEND_COL_WIDTH_IN)))
    rows = math.ceil(len(handles) / ncol)
    ax.legend(
        handles=handles,
        loc="lower right",
        bbox_to_anchor=(1.0, 1.01),
        ncol=ncol,
        fontsize=_FONT,
        frameon=False,
        borderaxespad=0.0,
    )
    ax.set_title(
        title if title is not None else "Longitudinal section",
        loc="left",
        pad=_TITLE_PAD + _TITLE_ROW_PAD * rows if rows > 1 else _TITLE_PAD,
    )

    ve_text = _exaggeration_text(ax, vertical_exaggeration)
    ax.text(
        0.995,
        0.02,
        f"vertical exaggeration: {ve_text}",
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=_FONT,
        color=_TEXT_COLOUR,
        bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.8),
        zorder=11,
    )

    if show_labels and segments:
        _label_band(ax, segments, seg_run, ch, invert)
    else:
        ax.set_xlabel("Chainage [m]")
    return fig, ax
