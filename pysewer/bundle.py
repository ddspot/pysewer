# SPDX-FileCopyrightText: 2025 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""
Self-contained, serialisable result of a pysewer run.

:class:`NetworkBundle` flattens a designed sewer graph (a ``networkx.DiGraph``
returned by :func:`pysewer.route_and_design`) into two GeoDataFrames, a
JSON-able metadata dict and the longitudinal profiles. The bundle is computed
once, in the projected CRS of the DEM, and does not hold a reference to the
graph. Writers, readers and metrics build on this schema (schema version
``SCHEMA_VERSION``).

Schema
------
``pipes`` (LineString, flow direction ``from_node`` -> ``to_node``), columns
in the order of :data:`PIPE_COLUMNS`:

* ids: ``pipe_id`` (``P000001``), ``from_node``, ``to_node`` (node ids)
* ``length`` (m), ``pipe_type`` (gravity / pressurized / undesigned),
  ``private`` (bool)
* hydraulics: ``diameter`` (m), ``peak_flow`` (m3/s), ``velocity`` (m/s),
  ``d_over_D``
* levels (m): ``ground_*``, ``invert_*``, ``depth_*`` (ground - invert),
  ``crown_*`` (invert + diameter) at ``_up`` and ``_down`` ends
* slopes (m/m): ``invert_slope`` (negative = falling in flow direction),
  ``ground_slope`` (from preprocessing); ``mean_td``
* ``hydraulic_violations``, ``constraint_violations`` (comma-joined),
  ``n_violations``
* input attributes: ``road_name``, ``highway``

``nodes`` (Point), columns in the order of :data:`NODE_COLUMNS`: ``node_id``
(``N000001``), ``role`` (sink > building > pumping_station > lifting_station >
junction), ``node_type``, ``ground``, ``invert``, ``depth``, ``peak_flow``,
``upstream_pe`` (Int64), ``static_head``, flags (``pumping_station``,
``lifting_station``, ``private_pump``, ``onsite``, ``unrouted``) and
``population`` (buildings, when the input has such an attribute). Node ids
are dense (``N000001`` .. ``N{n:06d}``), assigned after isolated non-building
nodes are dropped; a node is kept when it has an edge, is a building, a sink
or an unrouted terminal. ``metadata["sinks"]`` and
``metadata["unrouted_terminals"]`` only hold ids present in ``nodes``.

Population is read, in this order, from the configured
``optimization.inhabitants_dwelling_attribute_name``, ``block_population``,
``population``, ``pe``, ``Einw_GEB`` (NaN otherwise);
``metadata["population_attribute"]`` is the attribute used for most buildings
(None if none). :data:`PIPE_DTYPES` and :data:`NODE_DTYPES` give the column
dtypes; :func:`coerce_dtypes` applies them (also for empty tables).

``profiles`` maps ``pipe_id`` to ``{"ground": [[s, z], ...], "invert":
[[s, z], ...]}`` with ``s`` in metres from ``from_node``.

``metadata`` is ``json.dumps``-able (schema_version, name, pysewer_version,
created_at, crs_wkt, crs_epsg, units, config, sinks, station_aware,
unrouted_terminals, counts, population_attribute, provenance). Extra keys
passed in ``inputs`` are kept under ``provenance["extra"]``.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime
import logging
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from shapely.geometry import Point

from pysewer.config import manager
from pysewer.helper import get_edge_gdf, get_edge_keys

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"

PIPE_COLUMNS = (
    "pipe_id",
    "from_node",
    "to_node",
    "length",
    "pipe_type",
    "private",
    "diameter",
    "peak_flow",
    "velocity",
    "d_over_D",
    "ground_up",
    "ground_down",
    "invert_up",
    "invert_down",
    "depth_up",
    "depth_down",
    "crown_up",
    "crown_down",
    "invert_slope",
    "ground_slope",
    "mean_td",
    "hydraulic_violations",
    "constraint_violations",
    "n_violations",
    "road_name",
    "highway",
)

NODE_COLUMNS = (
    "node_id",
    "role",
    "node_type",
    "ground",
    "invert",
    "depth",
    "peak_flow",
    "upstream_pe",
    "static_head",
    "pumping_station",
    "lifting_station",
    "private_pump",
    "onsite",
    "unrouted",
    "population",
)

_PIPE_STR = (
    "pipe_id",
    "from_node",
    "to_node",
    "pipe_type",
    "hydraulic_violations",
    "constraint_violations",
    "road_name",
    "highway",
)
PIPE_DTYPES: dict[str, str] = {
    c: ("str" if c in _PIPE_STR else "float64") for c in PIPE_COLUMNS
}
PIPE_DTYPES["private"] = "bool"
PIPE_DTYPES["n_violations"] = "int64"

NODE_DTYPES: dict[str, str] = {
    "node_id": "str",
    "role": "str",
    "node_type": "str",
    "ground": "float64",
    "invert": "float64",
    "depth": "float64",
    "peak_flow": "float64",
    "upstream_pe": "Int64",
    "static_head": "float64",
    "pumping_station": "bool",
    "lifting_station": "bool",
    "private_pump": "bool",
    "onsite": "bool",
    "unrouted": "bool",
    "population": "float64",
}

# Input attribute names (lower case) that map to the kept bundle attributes.
_ROAD_NAME_KEYS = ("name", "roadnamee", "roadnamea")
_HIGHWAY_KEYS = ("highway", "subtypelbl")
# Population attributes after the configured one (engine order).
_POPULATION_KEYS = ("block_population", "population", "pe", "einw_geb")

_UNITS = {
    "length": "m",
    "elevation": "m",
    "diameter": "m",
    "flow": "m3/s",
    "velocity": "m/s",
    "slope": "m/m",
}

_NAN = float("nan")


def _is_missing(value: Any) -> bool:
    """Return True for None, NaN and pandas NA."""
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _num(value: Any) -> float:
    """Convert a scalar to a python float (NaN when missing or not numeric)."""
    if _is_missing(value):
        return _NAN
    try:
        return float(value)
    except (TypeError, ValueError):
        return _NAN


def _flag(value: Any) -> bool:
    """Return True for True, ``np.bool_(True)`` and numbers equal to 1."""
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, float, np.integer, np.floating)):
        return bool(value == 1)
    return False


def coerce_dtypes(df: pd.DataFrame, dtypes: dict[str, str]) -> pd.DataFrame:
    """
    Return ``df`` with the columns of ``dtypes`` cast to the given dtypes.

    Columns missing from ``df`` are skipped (the geometry column is never
    touched). Missing text values become empty strings and missing flags
    False. Use with :data:`PIPE_DTYPES` or :data:`NODE_DTYPES`.
    """
    for col, dtype in dtypes.items():
        if col not in df.columns:
            continue
        if dtype == "str":
            df[col] = df[col].where(df[col].notna(), "").astype("str")
        elif dtype == "bool":
            df[col] = df[col].where(df[col].notna(), False).astype(bool)
        else:
            df[col] = df[col].astype(dtype)
    return df


def _as_text(value: Any) -> str:
    """Convert an attribute (scalar or list) to a string; '; '-join for lists."""
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return "; ".join(str(v) for v in value if not _is_missing(v))
    if _is_missing(value):
        return ""
    return str(value)


def _first_text(record: dict, keys: tuple[str, ...]) -> str:
    """Return the first non-empty text among the case-insensitive ``keys``."""
    lower = {str(k).lower(): v for k, v in record.items()}
    for key in keys:
        if key in lower:
            text = _as_text(lower[key])
            if text:
                return text
    return ""


def _population(data: dict, keys: tuple[str, ...]) -> tuple[float, str | None]:
    """Return ``(value, attribute)`` of the first numeric case-insensitive key."""
    lower = {str(k).lower(): (k, v) for k, v in data.items()}
    for key in keys:
        if key in lower:
            num = _num(lower[key][1])
            if not math.isnan(num):
                return num, str(lower[key][0])
    return _NAN, None


def _join(value: Any) -> str:
    """Comma-join a violations list."""
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(v) for v in value)
    return "" if _is_missing(value) else str(value)


def _pairs(profile: Any) -> list[list[float]]:
    """Copy a profile into ``[[s, z], ...]`` of python floats."""
    if profile is None:
        return []
    return [[float(s), float(z)] for s, z in profile]


def _jsonable(obj: Any) -> Any:
    """Convert nested config / provenance values to JSON-able python types."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        return _jsonable(obj.item())
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    return str(obj)


def _pysewer_version() -> str:
    try:
        return importlib_metadata.version("pysewer")
    except importlib_metadata.PackageNotFoundError:
        return "unknown"


def _node_ids(nodes: Iterable) -> dict:
    """Map node key to dense ``N%06d``, ordered by rounded coordinates."""
    order = sorted(nodes, key=lambda n: (round(n[0], 3), round(n[1], 3), n))
    return {n: f"N{i:06d}" for i, n in enumerate(order, start=1)}


def _default_sinks(G: nx.DiGraph) -> list:
    wwtp = [n for n, t in G.nodes(data="node_type") if t == "wwtp"]
    if wwtp:
        return wwtp
    return [n for n in G.nodes if G.out_degree(n) == 0 and G.in_degree(n) > 0]


def _provenance(inputs: dict | None) -> dict:
    blank = {"source": None, "path": None, "url": None}
    inputs = dict(inputs or {})
    layers = {
        k: {
            **blank,
            **{
                a: (str(b) if isinstance(b, Path) else b)
                for a, b in (inputs.pop(k, None) or {}).items()
            },
        }
        for k in ("dem", "roads", "buildings")
    }
    out = {
        "inputs": layers,
        "dem_source": inputs.pop("dem_source", None),
        "fetched_at": inputs.pop("fetched_at", None),
        "bbox_wgs84": inputs.pop("bbox_wgs84", None),
    }
    out["extra"] = inputs
    return _jsonable(out)


def _pipe_records(G: nx.DiGraph, ids: dict) -> tuple[list[dict], dict]:
    """Build pipe rows and profiles from the graph (no mutation)."""
    keys = get_edge_keys(G)
    if not keys:
        return [], {}
    edge_gdf = get_edge_gdf(G, detailed=True)
    records = edge_gdf.to_dict("records")
    rows, profiles = [], {}
    for (u, v), rec in zip(keys, records, strict=True):
        geom = rec["geometry"]
        length = float(geom.length)
        ground = _pairs(
            rec.get("profile") if isinstance(rec.get("profile"), list) else None
        )
        raw_inv = rec.get("trench_depth_profile")
        invert = _pairs(raw_inv) if isinstance(raw_inv, (list, tuple)) else []
        pressurized = rec.get("pressurized")
        if _is_missing(pressurized):
            pipe_type = "undesigned"
        else:
            pipe_type = "pressurized" if _flag(pressurized) else "gravity"
        diameter = _num(rec.get("diameter"))
        g_up, g_dn = (ground[0][1], ground[-1][1]) if ground else (_NAN, _NAN)
        i_up, i_dn = (invert[0][1], invert[-1][1]) if invert else (_NAN, _NAN)
        hyd = rec.get("hydraulic_violations")
        con = rec.get("constraint_violations")
        hyd = list(hyd) if isinstance(hyd, (list, tuple, set)) else []
        con = list(con) if isinstance(con, (list, tuple, set)) else []
        rows.append(
            {
                "from_node": ids[u],
                "to_node": ids[v],
                "length": length,
                "pipe_type": pipe_type,
                "private": _flag(rec.get("private_sewer")),
                "diameter": diameter,
                "peak_flow": _num(rec.get("peak_flow")),
                "velocity": _num(rec.get("velocity")),
                "d_over_D": _num(rec.get("d_over_D")),
                "ground_up": g_up,
                "ground_down": g_dn,
                "invert_up": i_up,
                "invert_down": i_dn,
                "depth_up": g_up - i_up,
                "depth_down": g_dn - i_dn,
                "crown_up": i_up + diameter,
                "crown_down": i_dn + diameter,
                "invert_slope": (i_dn - i_up) / length if length > 0 else _NAN,
                "ground_slope": _num(rec.get("slope")),
                "mean_td": _num(rec.get("mean_td")),
                "hydraulic_violations": _join(hyd),
                "constraint_violations": _join(con),
                "n_violations": len(set(hyd) | set(con)),
                "road_name": _first_text(rec, _ROAD_NAME_KEYS),
                "highway": _first_text(rec, _HIGHWAY_KEYS),
                "geometry": geom,
                "_ground": ground,
                "_invert": invert,
            }
        )
    rows.sort(key=lambda r: (r["from_node"], r["to_node"]))
    for i, row in enumerate(rows, start=1):
        pid = f"P{i:06d}"
        row["pipe_id"] = pid
        ground, invert = row.pop("_ground"), row.pop("_invert")
        if ground or invert:
            profiles[pid] = {"ground": ground, "invert": invert}
    return rows, profiles


def _node_frame(
    G: nx.DiGraph,
    ids: dict,
    pipes: gpd.GeoDataFrame,
    unrouted: set,
    sink_keys: list,
    crs,
) -> tuple[gpd.GeoDataFrame, str | None]:
    """Build the node table for the nodes in ``ids``; return it and the population attribute."""
    pop_keys = _POPULATION_KEYS
    configured = manager.get_config().optimization.inhabitants_dwelling_attribute_name
    if configured:
        pop_keys = (str(configured).lower(), *pop_keys)
    used: Counter = Counter()
    # lowest incident pipe-end invert per node id
    inv: dict[str, float] = {}
    for _, p in pipes[["from_node", "invert_up", "to_node", "invert_down"]].iterrows():
        for nid, val in (
            (p["from_node"], p["invert_up"]),
            (p["to_node"], p["invert_down"]),
        ):
            if not math.isnan(val):
                inv[nid] = min(inv.get(nid, math.inf), float(val))
    sinks = {tuple(s) for s in sink_keys}
    rows = []
    for n in ids:
        d = G.nodes[n]
        node_type = d.get("node_type")
        node_type = "" if _is_missing(node_type) else str(node_type)
        is_building = node_type == "building"
        pump = _flag(d.get("pumping_station"))
        lift = _flag(d.get("lifting_station"))
        if n in sinks:
            role = "sink"
        elif is_building:
            role = "building"
        elif pump:
            role = "pumping_station"
        elif lift:
            role = "lifting_station"
        else:
            role = "junction"
        nid = ids[n]
        ground = _num(d.get("elevation"))
        invert = inv.get(nid, _NAN)
        upe = d.get("upstream_pe")
        pop, pop_attr = _population(d, pop_keys) if is_building else (_NAN, None)
        if pop_attr is not None:
            used[pop_attr] += 1
        rows.append(
            {
                "node_id": nid,
                "role": role,
                "node_type": node_type,
                "ground": ground,
                "invert": invert,
                "depth": ground - invert,
                "peak_flow": _num(d.get("peak_flow")),
                "upstream_pe": None if _is_missing(upe) else round(float(upe)),
                "static_head": _num(d.get("total_static_head"))
                if (pump or lift)
                else _NAN,
                "pumping_station": pump,
                "lifting_station": lift,
                "private_pump": bool(is_building and pump),
                "onsite": _flag(d.get("onsite")),
                "unrouted": n in unrouted,
                "population": pop,
                "geometry": Point(n[0], n[1]),
            }
        )
    rows.sort(key=lambda r: r["node_id"])
    df = pd.DataFrame(rows, columns=[*NODE_COLUMNS, "geometry"])
    df["upstream_pe"] = pd.array(df["upstream_pe"], dtype="Int64")
    df = coerce_dtypes(df, NODE_DTYPES)
    pop_attr = used.most_common(1)[0][0] if used else None
    return gpd.GeoDataFrame(df, geometry="geometry", crs=crs), pop_attr


@dataclass(frozen=True, eq=False)
class NetworkBundle:
    """
    Flat, serialisable view of a designed sewer network.

    Attributes
    ----------
    pipes : geopandas.GeoDataFrame
        One row per pipe (columns :data:`PIPE_COLUMNS`), projected CRS.
    nodes : geopandas.GeoDataFrame
        One row per node (columns :data:`NODE_COLUMNS`), projected CRS.
    metadata : dict
        JSON-able description of the run.
    profiles : dict
        ``{pipe_id: {"ground": [[s, z], ...], "invert": [[s, z], ...]}}``.
    """

    pipes: gpd.GeoDataFrame
    nodes: gpd.GeoDataFrame
    metadata: dict
    profiles: dict

    @property
    def name(self) -> str:
        """Name of the bundle."""
        return self.metadata.get("name", "network")

    @property
    def crs(self):
        """CRS of the bundle tables (``pyproj.CRS`` or None)."""
        return self.pipes.crs

    @classmethod
    def from_graph(
        cls,
        G: nx.DiGraph,
        *,
        inputs: dict | None = None,
        name: str = "network",
        sinks: list | None = None,
    ) -> NetworkBundle:
        """
        Build a bundle from a designed graph. The graph is not modified.

        Parameters
        ----------
        G : networkx.DiGraph
            Output of :func:`pysewer.route_and_design`.
        inputs : dict, optional
            Provenance: keys ``dem``, ``roads``, ``buildings`` (each a dict
            with ``source``, ``path``, ``url``), ``dem_source``,
            ``fetched_at``, ``bbox_wgs84``. Merged over None defaults; any other
            key goes to ``provenance["extra"]``.
        name : str
            Bundle name.
        sinks : list, optional
            Sink node keys. Keys not in the graph are ignored with a warning.
            Default: nodes with ``node_type == "wwtp"``, else nodes without
            out-edges but with in-edges.

        Returns
        -------
        NetworkBundle
        """
        crs = G.graph.get("crs")
        unrouted_keys = [tuple(t) for t in G.graph.get("unrouted_terminals") or []]
        unrouted = {n for n in unrouted_keys if n in G}
        if sinks is not None:
            sink_keys = [tuple(s) for s in sinks]
            missing = [s for s in sink_keys if s not in G]
            if missing:
                logger.warning(
                    "Sinks not in the graph are ignored: %s", sorted(missing)
                )
            sink_keys = [s for s in sink_keys if s in G]
        else:
            sink_keys = _default_sinks(G)
        keep = {
            n
            for n, t in G.nodes(data="node_type")
            if G.degree(n) > 0 or t == "building" or n in unrouted
        } | set(sink_keys)
        ids = _node_ids(keep)
        rows, profiles = _pipe_records(G, ids)
        df = pd.DataFrame(rows, columns=[*PIPE_COLUMNS, "geometry"])
        df = coerce_dtypes(df, PIPE_DTYPES)
        pipes = gpd.GeoDataFrame(df, geometry="geometry", crs=crs)
        nodes, pop_attr = _node_frame(G, ids, pipes, unrouted, sink_keys, crs)

        station_aware = G.graph.get("station_aware")
        meta = {
            "schema_version": SCHEMA_VERSION,
            "name": name,
            "pysewer_version": _pysewer_version(),
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "crs_wkt": crs.to_wkt() if crs is not None else None,
            "crs_epsg": crs.to_epsg() if crs is not None else None,
            "units": dict(_UNITS),
            "config": _jsonable(dataclasses.asdict(manager.get_config())),
            "sinks": sorted(ids[s] for s in set(sink_keys)),
            "station_aware": _jsonable(copy.deepcopy(station_aware))
            if station_aware is not None
            else None,
            "unrouted_terminals": sorted(ids[n] for n in unrouted),
            "counts": {"pipes": len(pipes), "nodes": len(nodes)},
            "population_attribute": pop_attr,
            "provenance": _provenance(inputs),
        }
        logger.debug("Built NetworkBundle %s: %s", name, meta["counts"])
        return cls(pipes=pipes, nodes=nodes, metadata=meta, profiles=profiles)

    def to_wgs84(self) -> NetworkBundle:
        """
        Return a copy of the bundle in EPSG:4326 (lon, lat).

        Geometries are reprojected only; no value is recomputed and the
        profiles are unchanged. ``crs_wkt`` and ``crs_epsg`` describe the new
        CRS, ``source_crs_wkt`` keeps the original one.

        Raises
        ------
        ValueError
            If the bundle has no CRS.
        """
        if self.crs is None:
            raise ValueError("The bundle has no CRS; cannot convert to EPSG:4326.")
        meta = copy.deepcopy(self.metadata)
        meta.setdefault("source_crs_wkt", meta.get("crs_wkt"))
        pipes = self.pipes.to_crs(4326)
        nodes = self.nodes.to_crs(4326)
        meta["crs_wkt"] = pipes.crs.to_wkt()
        meta["crs_epsg"] = pipes.crs.to_epsg()
        return NetworkBundle(
            pipes=pipes,
            nodes=nodes,
            metadata=meta,
            profiles=copy.deepcopy(self.profiles),
        )
