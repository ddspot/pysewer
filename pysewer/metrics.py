# SPDX-FileCopyrightText: 2025 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""
Metrics and quality checks for a designed sewer network.

Both functions read a :class:`pysewer.bundle.NetworkBundle` (or a designed
``networkx.DiGraph``, which is converted with
:meth:`~pysewer.bundle.NetworkBundle.from_graph`). All lengths come from the
``length`` column of the bundle (metres along the pipe geometry), all
elevations and depths are in metres, flows in m3/s and velocities in m/s.

Functions
---------
network_metrics
    One JSON-able dictionary with counts, lengths, stations, diameters,
    depths, hydraulics, violations and graph statistics.
quality_checks
    One row per finding of a per-pipe / per-node check against the
    thresholds of the ``optimization`` configuration.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd

from pysewer.bundle import NetworkBundle

METRICS_SCHEMA = "metrics/1"

QUALITY_COLUMNS = (
    "element_type",
    "element_id",
    "check",
    "severity",
    "value",
    "limit",
    "unit",
    "message",
)

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_EPS = 1e-9


def _as_bundle(obj: Any) -> NetworkBundle:
    """Return ``obj`` as a bundle (a graph is converted, a bundle is kept)."""
    if isinstance(obj, NetworkBundle):
        return obj
    if isinstance(obj, nx.Graph):
        return NetworkBundle.from_graph(obj)
    raise TypeError(
        f"Expected a NetworkBundle or a networkx graph, got {type(obj).__name__}."
    )


def _f(value: Any) -> float | None:
    """Python float, or None when the value is missing or not finite."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _split(text: Any) -> list[str]:
    """Split a comma-joined violation string into names."""
    if not isinstance(text, str) or not text:
        return []
    return [t for t in text.split(",") if t]


def _count_violations(series: pd.Series) -> dict[str, int]:
    """Count pipes per violation name (most frequent first, then by name)."""
    counts: dict[str, int] = {}
    for text in series:
        for name in set(_split(text)):
            counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _diameter_stats(pipes: pd.DataFrame) -> tuple[dict[str, int], dict[str, float]]:
    """Return ``({str(d): n}, {str(d): length_m})`` sorted by diameter."""
    sub = pipes[pipes["diameter"].notna()]
    if sub.empty:
        return {}, {}
    count = sub.groupby("diameter").size()
    length = sub.groupby("diameter")["length"].sum()
    return (
        {str(float(d)): int(n) for d, n in sorted(count.items())},
        {str(float(d)): float(m) for d, m in sorted(length.items())},
    )


def _longest_flow_path(pipes: pd.DataFrame) -> float:
    """
    Longest pipe length from any node to its outlet, following ``to_node``.

    A node has at most one out-edge in a designed network. If a node has
    several, the longest continuation counts. Cycles are not followed twice.
    """
    out: dict[str, list[tuple[str, float]]] = {}
    for u, v, length in zip(
        pipes["from_node"], pipes["to_node"], pipes["length"], strict=True
    ):
        out.setdefault(u, []).append((v, float(length)))
    memo: dict[str, float] = {}

    def dist(start: str) -> float:
        stack = [start]
        on_path = {start}
        while stack:
            node = stack[-1]
            if node in memo:
                stack.pop()
                continue
            pending = [
                v for v, _ in out.get(node, []) if v not in memo and v not in on_path
            ]
            if pending:
                nxt = pending[0]
                stack.append(nxt)
                on_path.add(nxt)
                continue
            memo[node] = max(
                (length + memo.get(v, 0.0) for v, length in out.get(node, [])),
                default=0.0,
            )
            on_path.discard(node)
            stack.pop()
        return memo[start]

    return max((dist(n) for n in out), default=0.0)


def _connected_nodes(pipes: pd.DataFrame) -> set[str]:
    """Node ids that have at least one pipe."""
    return set(pipes["from_node"]) | set(pipes["to_node"])


def _unconnected_buildings(b: NetworkBundle) -> pd.Series:
    """Boolean mask over ``b.nodes``: building that is unrouted or has no pipe."""
    nodes = b.nodes
    with_pipe = _connected_nodes(b.pipes)
    return (nodes["role"] == "building") & (
        nodes["unrouted"] | ~nodes["node_id"].isin(with_pipe)
    )


def network_metrics(bundle: NetworkBundle | nx.Graph) -> dict:
    """
    Summarise a designed network in one JSON-able dictionary.

    Parameters
    ----------
    bundle : NetworkBundle or networkx.DiGraph
        A bundle, or a designed graph (converted with
        :meth:`NetworkBundle.from_graph`).

    Returns
    -------
    dict
        Python scalars, dicts and lists only. Values that are undefined (for
        example the mean depth of a network without gravity pipes) are None.
        Units are part of the key names (``_m``, ``_m3s``, ``_ms``).

        * ``schema``: ``"metrics/1"``.
        * Counts: ``n_pipes``, ``n_nodes``, ``buildings_total``,
          ``buildings_connected``, ``buildings_unconnected`` (a building that
          is unrouted or has no pipe).
        * Lengths in m, from the ``length`` column: ``total_length_m``,
          ``gravity_length_m``, ``pressurized_length_m``,
          ``undesigned_length_m``, ``private_length_m`` (pipes with
          ``private`` set; they are also part of the other totals).
        * Stations: ``n_pumping_stations`` (role ``pumping_station``, public
          pumps), ``n_lifting_stations`` (role ``lifting_station``),
          ``n_private_pumps`` (pumps in buildings).
        * Diameters: ``diameter_count`` and ``diameter_length_m`` (key
          ``str(diameter in m)``, all pipes with a diameter) and the same
          for gravity pipes: ``diameter_count_gravity``,
          ``diameter_length_m_gravity``.
        * Depth in m (gravity pipes): ``max_depth_m``, ``mean_depth_m``
          (length-weighted mean of the mean of the two end depths),
          ``excavation_proxy_m3_per_m_width`` = sum of ``length`` times mean
          end depth over all designed pipes (gravity and pressurized). It is
          the trench volume per metre of trench width. It is a proxy: it
          ignores the shape of the profile, the trench slope and the pipe
          wall.
        * Hydraulics (gravity pipes unless stated): ``peak_flow_max_m3s``
          (all pipes), ``max_d_over_D_gravity``, ``velocity_min_ms``,
          ``velocity_max_ms``.
        * Violations: ``hydraulic_violations`` and ``constraint_violations``
          as ``{name: number of pipes}``.
        * Graph: ``n_components`` (weak components among the nodes with at
          least one pipe), ``longest_flow_path_m`` (largest pipe length from
          a node to its outlet), ``n_sinks``.
    """
    b = _as_bundle(bundle)
    pipes, nodes = b.pipes, b.nodes
    ptype = pipes["pipe_type"]
    gravity = pipes[ptype == "gravity"]
    designed = pipes[ptype.isin(["gravity", "pressurized"])]
    length = pipes["length"]

    buildings = nodes["role"] == "building"
    unconnected = _unconnected_buildings(b)

    d_all, dl_all = _diameter_stats(pipes)
    d_grav, dl_grav = _diameter_stats(gravity)

    # depth: length-weighted mean of the two end depths
    def mean_end_depth(df: pd.DataFrame) -> pd.Series:
        return (df["depth_up"] + df["depth_down"]) / 2.0

    g_mean = mean_end_depth(gravity)
    g_ok = g_mean.notna()
    g_len = gravity.loc[g_ok, "length"]
    mean_depth = (
        float((g_mean[g_ok] * g_len).sum() / g_len.sum())
        if g_ok.any() and g_len.sum() > 0
        else None
    )
    max_depth = (
        _f(gravity[["depth_up", "depth_down"]].max().max()) if len(gravity) else None
    )
    d_mean = mean_end_depth(designed)
    d_ok = d_mean.notna()
    excavation = float((d_mean[d_ok] * designed.loc[d_ok, "length"]).sum())

    graph = nx.Graph()
    graph.add_edges_from(zip(pipes["from_node"], pipes["to_node"], strict=True))

    vel = gravity["velocity"]
    return {
        "schema": METRICS_SCHEMA,
        "n_pipes": len(pipes),
        "n_nodes": len(nodes),
        "buildings_total": int(buildings.sum()),
        "buildings_connected": int((buildings & ~unconnected).sum()),
        "buildings_unconnected": int(unconnected.sum()),
        "total_length_m": float(length.sum()),
        "gravity_length_m": float(gravity["length"].sum()),
        "pressurized_length_m": float(length[ptype == "pressurized"].sum()),
        "undesigned_length_m": float(length[ptype == "undesigned"].sum()),
        "private_length_m": float(length[pipes["private"]].sum()),
        "n_pumping_stations": int(
            ((nodes["role"] == "pumping_station") & ~nodes["private_pump"]).sum()
        ),
        "n_lifting_stations": int((nodes["role"] == "lifting_station").sum()),
        "n_private_pumps": int(nodes["private_pump"].sum()),
        "diameter_count": d_all,
        "diameter_length_m": dl_all,
        "diameter_count_gravity": d_grav,
        "diameter_length_m_gravity": dl_grav,
        "max_depth_m": max_depth,
        "mean_depth_m": mean_depth,
        "excavation_proxy_m3_per_m_width": excavation,
        "peak_flow_max_m3s": _f(pipes["peak_flow"].max()) if len(pipes) else None,
        "max_d_over_D_gravity": _f(gravity["d_over_D"].max()) if len(gravity) else None,
        "velocity_min_ms": _f(vel.min()) if len(gravity) else None,
        "velocity_max_ms": _f(vel.max()) if len(gravity) else None,
        "hydraulic_violations": _count_violations(pipes["hydraulic_violations"]),
        "constraint_violations": _count_violations(pipes["constraint_violations"]),
        "n_components": int(nx.number_connected_components(graph)),
        "longest_flow_path_m": float(_longest_flow_path(pipes)),
        "n_sinks": int((nodes["role"] == "sink").sum()),
    }


_THRESHOLDS = (
    "tmax",
    "tmin",
    "min_cover",
    "velocity_min",
    "velocity_max",
    "max_depth_ratio",
    "min_pipe_length",
    "small_sewer_flow_threshold",
)


def _thresholds(config: Any, b: NetworkBundle) -> SimpleNamespace:
    """
    Resolve the ``optimization`` thresholds.

    ``config`` is None (global config), ``"bundle"`` (``metadata["config"]``),
    a ``Config`` object, or a dict with an ``optimization`` section (or the
    section itself).
    """
    if config is None:
        from pysewer.config.manager import get_config

        section: Any = get_config().optimization
    elif isinstance(config, str):
        if config != "bundle":
            raise ValueError(
                f"config must be None, 'bundle' or a config, got {config!r}."
            )
        section = b.metadata["config"]["optimization"]
    elif isinstance(config, dict):
        section = config.get("optimization", config)
    else:
        section = getattr(config, "optimization", config)
    get = section.get if isinstance(section, dict) else lambda k: getattr(section, k)
    return SimpleNamespace(**{k: float(get(k)) for k in _THRESHOLDS})


def _rows(
    element_type: str,
    ids: pd.Series,
    check: str,
    severity: str,
    value: pd.Series | float,
    limit: float,
    unit: str,
    message: Any,
) -> pd.DataFrame:
    """Build the finding rows of one check (``message`` is a callable on value)."""
    n = len(ids)
    values = (
        value.to_numpy(dtype=float)
        if isinstance(value, pd.Series)
        else np.full(n, value, dtype=float)
    )
    return pd.DataFrame(
        {
            "element_type": element_type,
            "element_id": ids.to_numpy(dtype=object),
            "check": check,
            "severity": severity,
            "value": values,
            "limit": float(limit),
            "unit": unit,
            "message": [message(v) for v in values],
        },
        columns=list(QUALITY_COLUMNS),
    )


def quality_checks(
    bundle: NetworkBundle | nx.Graph, config: Any = None
) -> pd.DataFrame:
    """
    List the design findings of a network, one row per finding.

    Parameters
    ----------
    bundle : NetworkBundle or networkx.DiGraph
        A bundle, or a designed graph (converted with
        :meth:`NetworkBundle.from_graph`).
    config : None, "bundle", Config or dict, optional
        Source of the thresholds (``optimization`` section). None uses the
        global configuration. ``"bundle"`` uses ``bundle.metadata["config"]``,
        the configuration of the run that made the bundle.

    Returns
    -------
    pandas.DataFrame
        Columns ``element_type`` ("pipe" or "node"), ``element_id``,
        ``check``, ``severity`` ("error", "warning" or "info"), ``value``,
        ``limit``, ``unit`` and ``message``. Sorted by severity (error,
        warning, info), then ``element_id``. Empty (with the columns) when
        there is no finding. Missing (NaN) values never give a finding.

    Notes
    -----
    Checks on gravity pipes: ``depth_exceeds_tmax`` (error; end depth above
    ``tmax``), ``cover_below_min`` (warning; cover over the pipe crown,
    ground minus crown, below ``min_cover``; pysewer's own ``min_cover``
    check does not use the crown), ``velocity_low`` (warning; only when the
    peak flow is at least ``small_sewer_flow_threshold``), ``velocity_high``
    (warning), ``fill_above_target`` (error; ``d_over_D`` above
    ``max_depth_ratio``), ``invert_rises`` (error; positive invert slope).
    Checks on all pipes: ``short_pipe`` (info; length below
    ``min_pipe_length``), ``engine_violation`` (warning; one row per item in
    ``hydraulic_violations`` and ``constraint_violations``, check name
    ``"engine:<item>"``) and ``undesigned_pipe`` (info). Node check:
    ``unrouted_building`` (error).
    """
    b = _as_bundle(bundle)
    t = _thresholds(config, b)
    pipes, nodes = b.pipes, b.nodes
    gravity = pipes[pipes["pipe_type"] == "gravity"]
    gid = gravity["pipe_id"]
    parts: list[pd.DataFrame] = []

    def add(df: pd.DataFrame) -> None:
        if len(df):
            parts.append(df)

    # depth above the trench-depth limit
    depth = gravity[["depth_up", "depth_down"]].max(axis=1)
    m = depth > t.tmax + _EPS
    add(
        _rows(
            "pipe",
            gid[m],
            "depth_exceeds_tmax",
            "error",
            depth[m],
            t.tmax,
            "m",
            lambda v: f"Trench depth {v:.2f} m is above the limit of {t.tmax:.2f} m.",
        )
    )

    # cover over crown (ground - crown) at the worse end
    cover = pd.concat(
        [
            gravity["depth_up"] - gravity["diameter"],
            gravity["depth_down"] - gravity["diameter"],
        ],
        axis=1,
    ).min(axis=1)
    m = cover < t.min_cover - _EPS
    add(
        _rows(
            "pipe",
            gid[m],
            "cover_below_min",
            "warning",
            cover[m],
            t.min_cover,
            "m",
            lambda v: (
                f"Cover over the pipe crown is {v:.2f} m, below {t.min_cover:.2f} m. "
                "The engine min_cover check does not use the crown."
            ),
        )
    )

    vel = gravity["velocity"]
    m = (vel < t.velocity_min - _EPS) & (
        gravity["peak_flow"] >= t.small_sewer_flow_threshold - _EPS
    )
    add(
        _rows(
            "pipe",
            gid[m],
            "velocity_low",
            "warning",
            vel[m],
            t.velocity_min,
            "m/s",
            lambda v: f"Velocity {v:.2f} m/s is below {t.velocity_min:.2f} m/s.",
        )
    )
    m = vel > t.velocity_max + _EPS
    add(
        _rows(
            "pipe",
            gid[m],
            "velocity_high",
            "warning",
            vel[m],
            t.velocity_max,
            "m/s",
            lambda v: f"Velocity {v:.2f} m/s is above {t.velocity_max:.2f} m/s.",
        )
    )

    fill = gravity["d_over_D"]
    m = fill > t.max_depth_ratio + _EPS
    add(
        _rows(
            "pipe",
            gid[m],
            "fill_above_target",
            "error",
            fill[m],
            t.max_depth_ratio,
            "",
            lambda v: f"Filling ratio d/D {v:.2f} is above {t.max_depth_ratio:.2f}.",
        )
    )

    slope = gravity["invert_slope"]
    m = slope > _EPS
    add(
        _rows(
            "pipe",
            gid[m],
            "invert_rises",
            "error",
            slope[m],
            0.0,
            "m/m",
            lambda v: f"The pipe invert rises in flow direction (slope {v:.4f} m/m).",
        )
    )

    m = pipes["length"] < t.min_pipe_length - _EPS
    add(
        _rows(
            "pipe",
            pipes.loc[m, "pipe_id"],
            "short_pipe",
            "info",
            pipes.loc[m, "length"],
            t.min_pipe_length,
            "m",
            lambda v: f"Pipe length {v:.2f} m is below {t.min_pipe_length:.2f} m.",
        )
    )

    eng: list[tuple[str, str]] = []
    for pid, hyd, con in zip(
        pipes["pipe_id"],
        pipes["hydraulic_violations"],
        pipes["constraint_violations"],
        strict=True,
    ):
        for item in dict.fromkeys(_split(hyd) + _split(con)):
            eng.append((pid, item))
    if eng:
        add(
            pd.DataFrame(
                {
                    "element_type": "pipe",
                    "element_id": [p for p, _ in eng],
                    "check": ["engine:" + i for _, i in eng],
                    "severity": "warning",
                    "value": np.nan,
                    "limit": np.nan,
                    "unit": "",
                    "message": [
                        f"The design engine flagged: {_engine_text(i)}." for _, i in eng
                    ],
                },
                columns=list(QUALITY_COLUMNS),
            )
        )

    m = _unconnected_buildings(b)
    add(
        _rows(
            "node",
            nodes.loc[m, "node_id"],
            "unrouted_building",
            "error",
            np.nan,
            np.nan,
            "",
            lambda v: "The building has no connection to the sewer.",
        )
    )

    m = pipes["pipe_type"] == "undesigned"
    add(
        _rows(
            "pipe",
            pipes.loc[m, "pipe_id"],
            "undesigned_pipe",
            "info",
            np.nan,
            np.nan,
            "",
            lambda v: "The pipe has no hydraulic design.",
        )
    )

    if not parts:
        return pd.DataFrame(
            {c: pd.Series(dtype=object) for c in QUALITY_COLUMNS}
        ).astype({"value": float, "limit": float})
    out = pd.concat(parts, ignore_index=True)
    out["_sev"] = out["severity"].map(_SEVERITY_ORDER)
    out = out.sort_values(["_sev", "element_id", "check"], kind="mergesort").drop(
        columns="_sev"
    )
    return out.reset_index(drop=True)


# Plain-language text for the violation names of the design engine.
_ENGINE_TEXT = {
    "max_slope": "the slope is steeper than the maximum slope",
    "min_slope": "the slope is less than the minimum slope",
    "min_cover": "the cover is less than the minimum cover (engine check)",
    "min_pipe_length": "the pipe is shorter than the minimum length (engine check)",
    "velocity_min": "the velocity is below the minimum (engine check)",
    "velocity_max": "the velocity is above the maximum (engine check)",
    "depth_ratio": "the filling ratio d/D is above the target (engine check)",
    "no_diameter_meets_depth_ratio": (
        "no available diameter keeps the filling ratio d/D within the target"
    ),
    "gradient_below_dts": (
        "the gradient is below the minimum gradient for small flows"
    ),
}


def _engine_text(item: str) -> str:
    return _ENGINE_TEXT.get(item, f"the design engine reported {item}")


_CHECK_TEXT = {
    "depth_exceeds_tmax": "the trench is deeper than tmax",
    "velocity_low": "the velocity is below the minimum",
    "velocity_high": "the velocity is above the maximum",
    "fill_above_target": "the filling ratio d/D is above the target",
    "invert_rises": "the invert rises in flow direction",
    "short_pipe": "the pipe is shorter than the minimum length",
    "undesigned_pipe": "the pipe has no hydraulic design",
    "unrouted_building": "the building has no connection to the sewer",
}


def summarize_findings(
    findings: pd.DataFrame,
    config: Any = None,
    bundle: NetworkBundle | nx.Graph | None = None,
) -> list[dict]:
    """
    Summarise the output of :func:`quality_checks`, one entry per check.

    Parameters
    ----------
    findings : pandas.DataFrame
        Output of :func:`quality_checks`.
    config : None, "bundle", Config or dict, optional
        Source of the thresholds named in the messages. Use the same value
        as for :func:`quality_checks`.
    bundle : NetworkBundle or networkx.DiGraph, optional
        Needed only when ``config="bundle"``.

    Returns
    -------
    list of dict
        Keys ``check``, ``severity``, ``count`` and ``message``. Sorted by
        severity (error, warning, info), then count (largest first), then
        check name. Empty list for empty input.
    """
    if findings is None or len(findings) == 0:
        return []
    if isinstance(config, str) and bundle is None:
        raise ValueError("config='bundle' needs the bundle argument.")
    t = _thresholds(config, _as_bundle(bundle) if bundle is not None else None)
    groups = findings.groupby(["check", "severity", "element_type"]).size()
    out = []
    for (check, severity, etype), n in groups.items():
        n = int(n)
        noun = etype if n == 1 else f"{etype}s"
        has = "has" if n == 1 else "have"
        if check == "cover_below_min":
            msg = (
                f"{n} {noun} {has} less than {t.min_cover:g} m cover over the crown. "
                f"The minimum trench depth (tmin = {t.tmin:g} m) is measured to "
                "the invert, so the cover is tmin minus the diameter."
            )
        elif check.startswith("engine:"):
            msg = f"{n} {noun}: {_engine_text(check[7:])}."
        else:
            msg = f"{n} {noun}: {_CHECK_TEXT.get(check, check)}."
        out.append({"check": check, "severity": severity, "count": n, "message": msg})
    out.sort(key=lambda d: (_SEVERITY_ORDER[d["severity"]], -d["count"], d["check"]))
    return out
