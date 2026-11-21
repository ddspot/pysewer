# SPDX-FileCopyrightText: 2025 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""
Longitudinal section of a designed sewer along one flow path.

:func:`longitudinal_profile` walks from a start node down the flow direction to
a sink and returns a :class:`Profile`. The profile holds, per pipe, the ground
line, the pipe invert, the crown and the water level, all against the chainage
measured from the start node, plus markers for manholes, drops and stations.
The module has no plotting dependency. ``Profile.to_dict`` is the JSON
contract (schema ``profile/1``) used by the HTML report.

Elevations are metres a.s.l., chainage and diameter are metres.

Pressurised pipes: the engine stores two invert points (a straight line between
the end inverts), which can pass above the ground where the terrain dips. The
profile shows a display derivation instead: the invert follows the terrain at
the end depths: ``invert(s) = ground(s) - depth(s)`` at every ground vertex,
with ``depth`` interpolated linearly in chainage between ``depth_up = ground_up
- invert_up`` and ``depth_down = ground_down - invert_down``. For engine data
both depths equal ``tmin``, so the invert is ``ground - tmin``. The end
inverts always equal the stored ones, so markers do not change. This is a
display derivation, not engine data; the engine result and the bundle columns
are unchanged. If an end depth is not finite, ``tmin`` from
``bundle.metadata["config"]["optimization"]["tmin"]`` is used; if that is
missing too, the engine's two-point invert is kept.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from pysewer.bundle import NetworkBundle

SCHEMA = "profile/1"
UNITS = {
    "chainage": "m",
    "elevation": "m",
    "diameter": "m",
    "peak_flow": "m3/s",
    "velocity": "m/s",
    "d_over_D": "1",
}
_NAN = float("nan")
# Invert steps smaller than this at a manhole are rounding or minimum-grade
# artefacts, not drops (test data: gap between 0.003 m and 0.22 m).
DROP_THRESHOLD = 0.05
# Tolerance for "no lift" at a station.
_LIFT_TOL = 1e-6
_SNAP_TOL = 1e-3

Point = tuple[float, float]


def _f(value: Any) -> float:
    """Convert a value to a Python float, mapping None and failures to NaN."""
    if value is None:
        return _NAN
    try:
        return float(value)
    except (TypeError, ValueError):
        return _NAN


def _out(value: float | None) -> float | None:
    """Return a JSON-safe float, NaN and inf become None."""
    return None if value is None or not math.isfinite(value) else float(value)


def _pts_out(points: list[Point]) -> list[list[float | None]]:
    return [[_out(s), _out(z)] for s, z in points]


def _pts_in(points: Any) -> list[Point]:
    return [(_f(s), _f(z)) for s, z in (points or [])]


@dataclass(frozen=True)
class ProfileSegment:
    """
    One pipe of the flow path.

    Chainages ``s`` are global, measured from the start node. ``ground``,
    ``invert`` and ``crown`` are lists of ``(s, z)``; gravity pipes share the
    chainages of the DEM profile. A pressurised invert follows the ground
    at the end depths (``ground - depth(s)`` at every ground vertex, depth
    interpolated between the end depths; a display derivation, not engine
    data), so it has the same chainages as the ground. ``water`` is empty for
    pressurised pipes and when ``d_over_D`` is
    missing.
    """

    pipe_id: str
    from_node: str
    to_node: str
    pipe_type: str
    diameter: float
    chainage_start: float
    chainage_end: float
    ground: list[Point] = field(default_factory=list)
    invert: list[Point] = field(default_factory=list)
    crown: list[Point] = field(default_factory=list)
    peak_flow: float = _NAN
    velocity: float = _NAN
    d_over_D: float = _NAN
    water: list[Point] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a JSON-able dict (NaN becomes None)."""
        return {
            "pipe_id": self.pipe_id,
            "from_node": self.from_node,
            "to_node": self.to_node,
            "pipe_type": self.pipe_type,
            "diameter": _out(self.diameter),
            "chainage_start": _out(self.chainage_start),
            "chainage_end": _out(self.chainage_end),
            "ground": _pts_out(self.ground),
            "invert": _pts_out(self.invert),
            "crown": _pts_out(self.crown),
            "peak_flow": _out(self.peak_flow),
            "velocity": _out(self.velocity),
            "d_over_D": _out(self.d_over_D),
            "water": _pts_out(self.water),
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProfileSegment:
        """Rebuild a segment from :meth:`to_dict` output."""
        return cls(
            pipe_id=d["pipe_id"],
            from_node=d["from_node"],
            to_node=d["to_node"],
            pipe_type=d["pipe_type"],
            diameter=_f(d.get("diameter")),
            chainage_start=_f(d.get("chainage_start")),
            chainage_end=_f(d.get("chainage_end")),
            ground=_pts_in(d.get("ground")),
            invert=_pts_in(d.get("invert")),
            crown=_pts_in(d.get("crown")),
            peak_flow=_f(d.get("peak_flow")),
            velocity=_f(d.get("velocity")),
            d_over_D=_f(d.get("d_over_D")),
            water=_pts_in(d.get("water")),
        )


@dataclass(frozen=True)
class ProfileMarker:
    """
    A node on the flow path.

    ``kind`` is one of ``start``, ``manhole``, ``drop``, ``lifting_station``,
    ``pumping_station``, ``pump_relay`` (a pumping station that this path
    reaches through a pressure main, so it has no lift here) or ``sink``.
    ``invert_in`` is the end invert of the incoming path pipe (NaN at the
    start), ``invert_out`` the start invert of the outgoing pipe (NaN at the
    sink).
    """

    node_id: str
    chainage: float
    role: str
    ground: float
    invert_in: float
    invert_out: float
    kind: str

    def to_dict(self) -> dict:
        """Return a JSON-able dict (NaN becomes None)."""
        return {
            "node_id": self.node_id,
            "chainage": _out(self.chainage),
            "role": self.role,
            "ground": _out(self.ground),
            "invert_in": _out(self.invert_in),
            "invert_out": _out(self.invert_out),
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProfileMarker:
        """Rebuild a marker from :meth:`to_dict` output."""
        return cls(
            node_id=d["node_id"],
            chainage=_f(d.get("chainage")),
            role=d.get("role", ""),
            ground=_f(d.get("ground")),
            invert_in=_f(d.get("invert_in")),
            invert_out=_f(d.get("invert_out")),
            kind=d["kind"],
        )


@dataclass(frozen=True, eq=False)
class Profile:
    """
    Longitudinal profile along one flow path.

    Equality compares :meth:`to_dict`, so NaN values compare equal.

    Chainage is the sum of the pipe ``length`` column; ``length`` is
    authoritative over the last profile vertex. The first node is always
    ``start``, also if it is a station (its ``role`` keeps the station).
    ``crs_wkt`` is the projected CRS of the network, also if the profile was
    built from a WGS84 bundle. ``params`` holds the marker rules
    (``drop_threshold``, ``lift_tol``) and the pressurised-invert rule
    (``pressurized_invert`` = ``"ground_minus_interpolated_end_depth"``,
    ``tmin`` in m from the config, None if missing) so that other clients can check
    them.
    """

    node_ids: list[str]
    pipe_ids: list[str]
    segments: list[ProfileSegment]
    markers: list[ProfileMarker]
    total_length: float
    crs_wkt: str = ""
    start: str = ""
    sink: str = ""
    params: dict = field(default_factory=dict)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Profile):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    __hash__ = None  # type: ignore[assignment]

    def to_dict(self) -> dict:
        """Return the JSON contract (schema ``profile/1``), NaN as None."""
        return {
            "schema": SCHEMA,
            "units": dict(UNITS),
            "start": self.start,
            "sink": self.sink,
            "crs_wkt": self.crs_wkt,
            "params": dict(self.params),
            "total_length": _out(self.total_length),
            "node_ids": list(self.node_ids),
            "pipe_ids": list(self.pipe_ids),
            "segments": [s.to_dict() for s in self.segments],
            "markers": [m.to_dict() for m in self.markers],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Profile:
        """Rebuild a profile from :meth:`to_dict` output."""
        if d.get("schema") != SCHEMA:
            raise ValueError(f"unsupported profile schema {d.get('schema')!r}")
        return cls(
            node_ids=list(d["node_ids"]),
            pipe_ids=list(d["pipe_ids"]),
            segments=[ProfileSegment.from_dict(s) for s in d["segments"]],
            markers=[ProfileMarker.from_dict(m) for m in d["markers"]],
            total_length=_f(d.get("total_length")),
            crs_wkt=d.get("crs_wkt", ""),
            start=d.get("start", ""),
            sink=d.get("sink", ""),
            params=dict(d.get("params") or {}),
        )

    def to_arrays(self) -> dict[str, np.ndarray]:
        """
        Return plotting arrays with NaN separators between pipes.

        Each segment is resampled on the union of its ground and invert
        chainages (linear interpolation). One NaN row separates two segments, so every
        pipe is its own
        polyline and a station jump shows as a gap. ``water`` is NaN for
        pressurised pipes. ``segment`` holds the segment index (-1 on
        separators) and ``pressurized`` is a bool array.

        Returns
        -------
        dict of numpy.ndarray
            Keys ``chainage``, ``ground``, ``invert``, ``crown``, ``water``,
            ``segment``, ``pressurized``.
        """
        keys = ("chainage", "ground", "invert", "crown", "water")
        cols: dict[str, list] = {k: [] for k in keys}
        seg_idx: list[int] = []
        press: list[bool] = []

        for i, seg in enumerate(self.segments):
            series = {
                "ground": seg.ground,
                "invert": seg.invert,
                "crown": seg.crown,
                "water": seg.water,
            }
            grid = sorted(
                {
                    round(s, 9)
                    for pts in series.values()
                    for s, _ in pts
                    if math.isfinite(s)
                }
            )
            if not grid:
                continue
            if cols["chainage"]:
                for k in keys:
                    cols[k].append(np.array([_NAN]))
                seg_idx.append(-1)
                press.append(False)
            x = np.asarray(grid, dtype=float)
            cols["chainage"].append(x)
            for k, pts in series.items():
                good = [(s, z) for s, z in pts if math.isfinite(s) and math.isfinite(z)]
                if len(good) < 2:
                    cols[k].append(np.full(x.shape, _NAN))
                    continue
                xs, zs = zip(*good, strict=True)
                vals = np.interp(x, xs, zs)
                vals[(x < min(xs) - 1e-9) | (x > max(xs) + 1e-9)] = _NAN
                cols[k].append(vals)
            seg_idx.extend([i] * len(x))
            press.extend([seg.pipe_type == "pressurized"] * len(x))
        out = {k: (np.concatenate(v) if v else np.empty(0)) for k, v in cols.items()}
        out["segment"] = np.asarray(seg_idx, dtype=int)
        out["pressurized"] = np.asarray(press, dtype=bool)
        return out


def _resolve_node(bundle: NetworkBundle, ref: Any, what: str) -> str:
    """Map a node_id or an (x, y) tuple to a node_id of the bundle."""
    nodes = bundle.nodes
    if isinstance(ref, str):
        if ref not in set(nodes["node_id"]):
            raise ValueError(f"{what} node {ref!r} is not in the bundle")
        return ref
    try:
        x, y = float(ref[0]), float(ref[1])
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError(f"{what} must be a node_id or an (x, y) tuple") from exc
    xs = nodes.geometry.x.to_numpy()
    ys = nodes.geometry.y.to_numpy()
    d = np.hypot(xs - x, ys - y)
    if d.size == 0 or float(d.min()) > _SNAP_TOL:
        raise ValueError(f"no node within {_SNAP_TOL} m of {what} {ref!r}")
    return str(nodes["node_id"].iloc[int(d.argmin())])


def _tmin_from_metadata(metadata: Any) -> float:
    """Return ``config.optimization.tmin`` from bundle metadata, else NaN."""
    try:
        return _f(metadata["config"]["optimization"]["tmin"])
    except (KeyError, TypeError):
        return _NAN


def _pressurized_invert(
    ground: list[Point], inv: list[Point], c0: float, tmin: float
) -> list[Point]:
    """
    Return the display invert of a pressurised pipe, ``ground - depth(s)``.

    ``depth`` is interpolated between the end depths of the stored invert; a
    non-finite end depth uses ``tmin``; without ``tmin`` the stored invert is
    returned unchanged.
    """
    depth_up = ground[0][1] - inv[0][1]
    depth_down = ground[-1][1] - inv[-1][1]
    if not math.isfinite(depth_up):
        depth_up = tmin
    if not math.isfinite(depth_down):
        depth_down = tmin
    if not (math.isfinite(depth_up) and math.isfinite(depth_down)):
        return inv
    length = ground[-1][0] - c0
    out: list[Point] = []
    for s, z in ground:
        if not math.isfinite(z):
            continue
        frac = (s - c0) / length if length > 0 else 0.0
        out.append((s, z - (depth_up + (depth_down - depth_up) * frac)))
    return out


def _station_kind(node: Any) -> str | None:
    if bool(node.get("pumping_station", False)):
        return "pumping_station"
    if bool(node.get("lifting_station", False)):
        return "lifting_station"
    return None


def longitudinal_profile(
    bundle: Any,
    start_node: Any,
    sink: Any = None,
    *,
    drop_threshold: float = DROP_THRESHOLD,
) -> Profile:
    """
    Trace the flow path from ``start_node`` to a sink and build its profile.

    Parameters
    ----------
    bundle : NetworkBundle or networkx.DiGraph
        A bundle, or a designed graph that is first converted with
        :meth:`NetworkBundle.from_graph`.
    start_node : str or tuple of float
        ``node_id`` such as ``"N000057"``, or an ``(x, y)`` coordinate in the
        bundle CRS (nearest node within 1e-3 CRS units, i.e. 1 mm in a
        projected CRS; use node ids for a WGS84 bundle).
    sink : str or tuple of float, optional
        Expected end node. If given and not reached, ``ValueError`` is raised.
    drop_threshold : float, optional
        Smallest invert step in m (``invert_in - invert_out``) at a manhole
        that is marked as ``drop``. Default 0.05 m.

    Notes
    -----
    The invert of a pressurised pipe is drawn at ``ground(s) - depth(s)`` at
    every ground vertex instead of the engine's straight two-point line.
    ``depth`` is interpolated linearly in chainage between the end depths
    ``ground - invert`` of the stored inverts (both equal ``tmin`` for engine
    data), so the end inverts and the markers equal the stored values. A
    non-finite end depth falls back to ``tmin`` from
    ``bundle.metadata["config"]["optimization"]["tmin"]``; if that is missing,
    the two-point invert is kept. This is a display derivation; the engine data
    and the bundle are unchanged. The rule and ``tmin`` are recorded in
    ``Profile.params``.

    Returns
    -------
    Profile

    Raises
    ------
    ValueError
        Unknown start or sink, a node with more than one outgoing pipe, a
        cycle, or a path that does not reach ``sink``.
    """
    if not isinstance(bundle, NetworkBundle):
        bundle = NetworkBundle.from_graph(bundle)
    start = _resolve_node(bundle, start_node, "start")
    sink_id = _resolve_node(bundle, sink, "sink") if sink is not None else None

    pipes = bundle.pipes
    nodes = bundle.nodes.set_index("node_id", drop=False)
    out_idx: dict[str, list[int]] = {}
    for i, u in enumerate(pipes["from_node"].tolist()):
        out_idx.setdefault(u, []).append(i)

    path_nodes = [start]
    path_rows: list[Any] = []
    seen = {start}
    cur = start
    while cur in out_idx:
        rows = out_idx[cur]
        if len(rows) > 1:
            raise ValueError(
                f"node {cur} has {len(rows)} outgoing pipes; the network is not a tree"
            )
        row = pipes.iloc[rows[0]]
        nxt = str(row["to_node"])
        if nxt in seen:
            raise ValueError(f"cycle detected at node {nxt}")
        seen.add(nxt)
        path_rows.append(row)
        path_nodes.append(nxt)
        cur = nxt
    end = cur
    if sink_id is not None and end != sink_id:
        raise ValueError(f"flow path from {start} ends at {end}, not at {sink_id}")

    profiles = bundle.profiles
    tmin_cfg = _tmin_from_metadata(bundle.metadata)
    segments: list[ProfileSegment] = []
    c0 = 0.0
    for row in path_rows:
        pid = str(row["pipe_id"])
        length = _f(row["length"])
        diameter = _f(row["diameter"])
        ptype = str(row["pipe_type"])
        prof = profiles.get(pid)
        if prof:
            ground = [(c0 + _f(s), _f(z)) for s, z in prof.get("ground", [])]
            inv = [(c0 + _f(s), _f(z)) for s, z in prof.get("invert", [])]
        else:
            ground = [
                (c0, _f(row["ground_up"])),
                (c0 + length, _f(row["ground_down"])),
            ]
            inv = [
                (c0, _f(row["invert_up"])),
                (c0 + length, _f(row["invert_down"])),
            ]
            ground = [p for p in ground if math.isfinite(p[1])]
            inv = [p for p in inv if math.isfinite(p[1])]
        if ptype == "pressurized" and len(ground) >= 2 and inv:
            inv = _pressurized_invert(ground, inv, c0, tmin_cfg)
        crown = [(s, z + diameter) for s, z in inv]
        d_over_D = _f(row["d_over_D"])
        water: list[Point] = []
        if ptype == "gravity" and math.isfinite(d_over_D) and math.isfinite(diameter):
            water = [(s, z + d_over_D * diameter) for s, z in inv]
        segments.append(
            ProfileSegment(
                pipe_id=pid,
                from_node=str(row["from_node"]),
                to_node=str(row["to_node"]),
                pipe_type=ptype,
                diameter=diameter,
                chainage_start=c0,
                chainage_end=c0 + length,
                ground=ground,
                invert=inv,
                crown=crown,
                peak_flow=_f(row["peak_flow"]),
                velocity=_f(row["velocity"]),
                d_over_D=d_over_D,
                water=water,
            )
        )
        c0 += length

    markers: list[ProfileMarker] = []
    for i, nid in enumerate(path_nodes):
        node = nodes.loc[nid]
        inc = segments[i - 1] if i > 0 else None
        outg = segments[i] if i < len(segments) else None
        inv_in = inc.invert[-1][1] if inc is not None and inc.invert else _NAN
        inv_out = outg.invert[0][1] if outg is not None and outg.invert else _NAN
        if i == 0:
            kind = "start"
        elif outg is None:
            kind = "sink"
        else:
            kind = _station_kind(node) or "manhole"
            finite = math.isfinite(inv_in) and math.isfinite(inv_out)
            if kind == "manhole" and finite and inv_in - inv_out >= drop_threshold:
                kind = "drop"
            elif (
                kind == "pumping_station"
                and finite
                and inc.pipe_type == "pressurized"
                and abs(inv_out - inv_in) <= _LIFT_TOL
            ):
                # A pressure main discharges where the next main starts: on
                # this path the station pumps on without a lift.
                kind = "pump_relay"
        markers.append(
            ProfileMarker(
                node_id=nid,
                chainage=inc.chainage_end if inc is not None else 0.0,
                role=str(node["role"]),
                ground=_f(node["ground"]),
                invert_in=inv_in,
                invert_out=inv_out,
                kind=kind,
            )
        )

    return Profile(
        node_ids=path_nodes,
        pipe_ids=[s.pipe_id for s in segments],
        segments=segments,
        markers=markers,
        total_length=c0,
        crs_wkt=str(
            bundle.metadata.get("source_crs_wkt") or bundle.metadata.get("crs_wkt", "")
        ),
        start=start,
        sink=end,
        params={
            "drop_threshold": float(drop_threshold),
            "lift_tol": _LIFT_TOL,
            "pressurized_invert": "ground_minus_interpolated_end_depth",
            "tmin": _out(tmin_cfg),
        },
    )
