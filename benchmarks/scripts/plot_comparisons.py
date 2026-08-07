# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""Render comparison figures for the published-vs-current benchmarks.

For every work/benchmarks/results/compare_<case>.json whose two geodata
exports exist (written by pipeline_worker), produce
work/benchmarks/results/figures/<case>.png with:

- side-by-side network maps (v0.1.20 left, current right), pipes colored by
  diameter and width-scaled, pump/lifting stations marked, sink as red star
- diameter distribution (grouped bars) and station counts per version

    python benchmarks/scripts/plot_comparisons.py [case ...]
"""

import json
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS = REPO_ROOT / "work" / "benchmarks" / "results"
FIGURES = RESULTS / "figures"

plt.rcParams.update(
    {"font.family": "Arial", "font.size": 9, "axes.titlesize": 10, "figure.dpi": 100}
)

SINK_COLOR = "#CC3311"
PUMP_COLOR = "#EE6677"
LIFT_COLOR = "#228833"
VERSION_LABELS = {"v0120": "published v0.1.20", "current": "current main"}


def diameter_colors(diameters):
    """Consistent discrete Blues ramp over the union of diameters."""
    cmap = plt.get_cmap("Blues")
    return {
        d: cmap(0.35 + 0.6 * i / max(1, len(diameters) - 1))
        for i, d in enumerate(sorted(diameters))
    }


def plot_map(ax, gpkg_path, color_by, sink, title):
    pipes = gpd.read_file(gpkg_path, layer="pipes")
    for diam, group in pipes.groupby("diameter"):
        group.plot(
            ax=ax,
            color=color_by[diam],
            linewidth=max(0.6, diam * 6),
            label=f"D {diam} m ({len(group)})",
        )
    try:
        stations = gpd.read_file(gpkg_path, layer="stations")
    except Exception:
        stations = None
    if stations is not None and len(stations):
        for kind, color, marker in (
            ("pumping_station", PUMP_COLOR, "^"),
            ("lifting_station", LIFT_COLOR, "^"),
        ):
            sel = stations[stations["kind"] == kind]
            if len(sel):
                sel.plot(
                    ax=ax, color=color, marker=marker, markersize=28,
                    edgecolor="white", linewidth=0.4, zorder=10,
                )
    ax.plot(*sink, marker="*", color=SINK_COLOR, markersize=15, zorder=11)
    ax.set_title(title)
    ax.set_aspect("equal")
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")
    ax.tick_params(labelsize=7)
    ax.legend(loc="best", fontsize=7, framealpha=0.9)
    return pipes, stations


def plot_case(case_json):
    comparison = json.loads(case_json.read_text())
    case = comparison["case"]
    sink = comparison["sink"]
    paths = {}
    for mode in ("v0120", "current"):
        geo = comparison[mode].get("geodata")
        if not geo or not Path(geo).exists():
            print(f"{case}: no geodata for {mode} — skipped")
            return None
        paths[mode] = Path(geo)

    all_diams = set()
    for p in paths.values():
        all_diams |= set(gpd.read_file(p, layer="pipes")["diameter"].unique())
    color_by = diameter_colors(all_diams)

    fig = plt.figure(figsize=(13, 8.5))
    gs = fig.add_gridspec(2, 2, height_ratios=[3, 1], hspace=0.28, wspace=0.15)
    data = {}
    for col, mode in enumerate(("v0120", "current")):
        ax = fig.add_subplot(gs[0, col])
        pipes, stations = plot_map(
            ax, paths[mode], color_by, sink, f"{case} — {VERSION_LABELS[mode]}"
        )
        n_pump = int((stations["kind"] == "pumping_station").sum()) if stations is not None else 0
        n_lift = int((stations["kind"] == "lifting_station").sum()) if stations is not None else 0
        data[mode] = {"pipes": pipes, "pump": n_pump, "lift": n_lift}

    # diameter distribution, grouped bars
    ax_hist = fig.add_subplot(gs[1, 0])
    diams = sorted(all_diams)
    x = np.arange(len(diams))
    for i, mode in enumerate(("v0120", "current")):
        counts = data[mode]["pipes"]["diameter"].value_counts()
        ax_hist.bar(
            x + (i - 0.5) * 0.38,
            [counts.get(d, 0) for d in diams],
            width=0.36,
            color="#AA3377" if mode == "v0120" else "#4477AA",
            label=VERSION_LABELS[mode],
        )
    ax_hist.set_xticks(x, [f"{d}" for d in diams])
    ax_hist.set_xlabel("Diameter (m)")
    ax_hist.set_ylabel("Pipe count")
    ax_hist.legend(fontsize=8)

    # station counts
    ax_st = fig.add_subplot(gs[1, 1])
    x = np.arange(2)
    for i, mode in enumerate(("v0120", "current")):
        ax_st.bar(
            x + (i - 0.5) * 0.38,
            [data[mode]["pump"], data[mode]["lift"]],
            width=0.36,
            color="#AA3377" if mode == "v0120" else "#4477AA",
            label=VERSION_LABELS[mode],
        )
    ax_st.set_xticks(x, ["pumping stations", "lifting stations"])
    ax_st.set_ylabel("Count")
    handles = [
        Line2D([], [], color=PUMP_COLOR, marker="^", ls="", label="pumping station"),
        Line2D([], [], color=LIFT_COLOR, marker="^", ls="", label="lifting station"),
        Line2D([], [], color=SINK_COLOR, marker="*", ls="", label="sink (WWTP)"),
    ]
    ax_st.legend(handles=handles + ax_st.get_legend_handles_labels()[0], fontsize=7)

    FIGURES.mkdir(parents=True, exist_ok=True)
    out = FIGURES / f"{case}.png"
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"{case}: {out}")
    return out


def main():
    wanted = set(sys.argv[1:])
    case_jsons = sorted(RESULTS.glob("compare_*.json"))
    case_jsons = [
        p
        for p in case_jsons
        if not p.name.endswith((".v0120.json", ".current.json", ".spec.json"))
        and (not wanted or p.stem.removeprefix("compare_") in wanted)
    ]
    if not case_jsons:
        sys.exit("no comparison JSONs found")
    for cj in case_jsons:
        try:
            plot_case(cj)
        except Exception as exc:
            print(f"{cj.name}: plot failed — {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
