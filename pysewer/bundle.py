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
import json
import logging
import math
import re
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
from pyproj import CRS
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

FORMATS = ("gpkg", "geojson", "parquet")
# Columns that share the rounding of another column (export.round_decimals).
_DECIMAL_ALIAS = {"invert_slope": "slope", "ground_slope": "slope"}
_FIONA_TYPES = {
    "str": "str",
    "float64": "float",
    "bool": "bool",
    "int64": "int",
    "Int64": "int",
}
_METADATA_LAYER = "pysewer_metadata"


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


def _finite_or_none(obj: Any) -> Any:
    """Replace non-finite floats by None, recursively (strict JSON)."""
    if isinstance(obj, dict):
        return {k: _finite_or_none(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite_or_none(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def _dumps(obj: Any) -> str:
    """Strict JSON text (no NaN / Infinity)."""
    return json.dumps(_finite_or_none(obj), allow_nan=False, ensure_ascii=False)


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(_dumps(obj) + "\n", encoding="utf-8")


def _nan_profiles(profiles: dict) -> dict:
    """Turn null levels / chainages of a profiles dict back into NaN."""
    return {
        pid: {
            kind: [[_NAN if v is None else v for v in pt] for pt in pts]
            for kind, pts in prof.items()
        }
        for pid, prof in profiles.items()
    }


def _decimals_for(columns: Iterable[str]) -> dict:
    """
    Per-column rounding digits from ``export.round_decimals`` (``{}`` = off).

    ``invert_slope`` and ``ground_slope`` use the ``slope`` setting when they
    have none of their own.
    """
    rd = manager.get_config().export.round_decimals
    if not rd:
        return {}
    default = rd.get("default")
    out = {"default": default}
    for col in columns:
        alias = _DECIMAL_ALIAS.get(col)
        out[col] = rd.get(col, rd.get(alias, default) if alias else default)
    return out


def _field_types(dtypes: dict[str, str]) -> dict[str, str]:
    return {c: _FIONA_TYPES[d] for c, d in dtypes.items()}


def _round_profiles(profiles: dict, digits: int | None) -> dict:
    if digits is None:
        return profiles
    return {
        pid: {
            kind: [[round(v, digits) for v in pt] for pt in pts]
            for kind, pts in prof.items()
        }
        for pid, prof in profiles.items()
    }


def _check_schema_version(meta: dict) -> None:
    version = meta.get("schema_version")
    match = re.fullmatch(r"(\d+)\.(\d+)", version) if isinstance(version, str) else None
    if match is None:
        raise ValueError(
            f"Bundle has a missing or invalid schema_version: {version!r}."
        )
    major, minor = int(match[1]), int(match[2])
    cur_major, cur_minor = (int(x) for x in SCHEMA_VERSION.split("."))
    if major != cur_major:
        raise ValueError(
            f"Unsupported bundle schema_version {version} "
            f"(this pysewer reads {cur_major}.x)."
        )
    if minor > cur_minor:
        logger.warning(
            "Bundle schema_version %s is newer than %s; unknown columns are kept.",
            version,
            SCHEMA_VERSION,
        )


def _conform(
    gdf: gpd.GeoDataFrame, columns: tuple[str, ...], dtypes: dict[str, str], what: str
) -> gpd.GeoDataFrame:
    """Order columns (known, extras in file order, geometry), set dtypes."""
    if len(gdf) == 0 and not set(columns) <= set(gdf.columns):
        df = pd.DataFrame(columns=[*columns, "geometry"])
        df = coerce_dtypes(df, dtypes)
        return gpd.GeoDataFrame(df, geometry="geometry", crs=gdf.crs)
    missing = [c for c in columns if c not in gdf.columns]
    if missing:
        raise ValueError(f"The {what} table lacks columns: {missing}.")
    if gdf.geometry.name != "geometry":
        gdf = gdf.rename_geometry("geometry")
    extras = [c for c in gdf.columns if c not in columns and c != "geometry"]
    gdf = gdf[[*columns, *extras, "geometry"]].reset_index(drop=True)
    return coerce_dtypes(gdf, dtypes)


def _resolve_format(path: Path, format: str | None) -> str:
    if format is None:
        if path.suffix.lower() == ".gpkg":
            return "gpkg"
        raise ValueError("pass format= ('gpkg', 'geojson' or 'parquet').")
    if format not in FORMATS:
        raise ValueError(f"Unknown format {format!r}; use one of {FORMATS}.")
    return format


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

    # ------------------------------------------------------------------ I/O

    def _table_decimals(self) -> tuple[dict, dict, int | None]:
        """Rounding for pipes, nodes and the profile digits (export rounding)."""
        pipes = _decimals_for(self.pipes.columns)
        nodes = _decimals_for(self.nodes.columns)
        return pipes, nodes, pipes.get("default")

    def _metadata_for_write(self) -> dict:
        """Copy of the metadata with ``counts`` taken from the actual tables."""
        meta = copy.deepcopy(self.metadata)
        meta["counts"] = {"pipes": len(self.pipes), "nodes": len(self.nodes)}
        return meta

    def to_gpkg(self, path: str | Path) -> Path:
        """
        Write the bundle to a GeoPackage; an existing file is replaced.

        Layers: ``pipes``, ``nodes`` (projected CRS) and the attribute table
        ``pysewer_metadata`` (rows ``metadata`` and ``profiles``, JSON text;
        the metadata is in the projected CRS, ``counts`` are refreshed from
        the tables on a copy).
        Floats and profiles are rounded as in
        :func:`pysewer.export.write_gdf_to_gpkg` (``export.round_decimals``).

        Returns
        -------
        pathlib.Path
        """
        import fiona

        from pysewer.export import write_gdf_to_gpkg

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        pipe_dec, node_dec, digits = self._table_decimals()
        write_gdf_to_gpkg(
            self.pipes,
            str(path),
            layer="pipes",
            decimals=pipe_dec,
            field_types=_field_types(PIPE_DTYPES),
            geometry_type="LineString",
        )
        write_gdf_to_gpkg(
            self.nodes,
            str(path),
            layer="nodes",
            decimals=node_dec,
            field_types=_field_types(NODE_DTYPES),
            geometry_type="Point",
        )
        schema = {"geometry": "None", "properties": {"key": "str", "value": "str"}}
        rows = (
            ("metadata", _dumps(self._metadata_for_write())),
            ("profiles", _dumps(_round_profiles(self.profiles, digits))),
        )
        with fiona.open(
            str(path), mode="w", driver="GPKG", layer=_METADATA_LAYER, schema=schema
        ) as dst:
            for key, value in rows:
                dst.write(
                    {"geometry": None, "properties": {"key": key, "value": value}}
                )
        logger.info("Wrote bundle %s to %s", self.name, path)
        return path

    def to_geojson_dict(self) -> dict:
        """
        Return the GeoJSON content as plain, strict-JSON Python objects.

        Same content as :meth:`to_geojson` (EPSG:4326, lon/lat, RFC 7946,
        7 coordinate decimals, same rounding and column types).

        Returns
        -------
        dict
            ``pipes`` and ``nodes`` (FeatureCollections), ``metadata`` (of
            :meth:`to_wgs84`: ``crs_epsg`` 4326 plus ``source_crs_wkt``;
            ``counts`` refreshed) and ``profiles`` (rounded, NaN as ``None``).
            ``json.dumps(..., allow_nan=False)`` always succeeds.

        Raises
        ------
        ValueError
            If the bundle has no CRS.

        Notes
        -----
        GDAL writes string values that parse as a JSON array as native
        arrays in GeoJSON. Bundle text columns do not hold such strings.
        """
        import fiona

        from pysewer.export import (
            GEOJSON_OPEN_OPTIONS,
            _profile_decimals,
            _properties,
            _write_features,
            round_float_columns,
        )

        wgs = self.to_wgs84()
        pipe_dec, node_dec, digits = self._table_decimals()

        def collection(gdf, decimals, dtypes, geometry_type):
            gdf = round_float_columns(gdf, decimals)
            values, types = _properties(
                gdf, _profile_decimals(decimals), _field_types(dtypes)
            )
            schema = {"geometry": geometry_type, "properties": types}
            with fiona.MemoryFile() as mem:
                with mem.open(
                    mode="w",
                    schema=schema,
                    **GEOJSON_OPEN_OPTIONS,
                ) as dst:
                    _write_features(dst, gdf, values)
                return json.loads(mem.read().decode("utf-8"))

        meta = wgs.metadata
        meta["counts"] = {"pipes": len(self.pipes), "nodes": len(self.nodes)}
        return _finite_or_none(
            {
                "pipes": collection(wgs.pipes, pipe_dec, PIPE_DTYPES, "LineString"),
                "nodes": collection(wgs.nodes, node_dec, NODE_DTYPES, "Point"),
                "metadata": meta,
                "profiles": _round_profiles(self.profiles, digits),
            }
        )

    def to_geojson(self, directory: str | Path) -> Path:
        """
        Write RFC 7946 GeoJSON (EPSG:4326, lon/lat) to a directory.

        Files: ``pipes.geojson``, ``nodes.geojson``, ``metadata.json`` and
        ``profiles.json``; the content is that of :meth:`to_geojson_dict`.
        ``metadata.json`` is the :meth:`to_wgs84` metadata (``crs_epsg`` 4326
        plus ``source_crs_wkt``, ``counts`` refreshed from the tables).
        Other files in the directory are left alone. Floats and profiles are
        rounded as for GeoPackage; coordinates have 7 decimals. GDAL writes
        list-like JSON strings as native arrays.

        Raises
        ------
        ValueError
            If the bundle has no CRS.

        Returns
        -------
        pathlib.Path
            The directory.
        """
        content = self.to_geojson_dict()
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        for key, name in (
            ("pipes", "pipes.geojson"),
            ("nodes", "nodes.geojson"),
            ("metadata", "metadata.json"),
            ("profiles", "profiles.json"),
        ):
            _write_json(directory / name, content[key])
        logger.info("Wrote bundle %s to %s", self.name, directory)
        return directory

    def to_parquet(self, directory: str | Path) -> Path:
        """
        Write GeoParquet (projected CRS, no rounding) to a directory.

        Files: ``pipes.parquet``, ``nodes.parquet``, ``metadata.json``
        (projected CRS; ``counts`` refreshed from the tables) and
        ``profiles.json``. Other files in the directory are left alone.

        Returns
        -------
        pathlib.Path
            The directory.
        """
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.pipes.to_parquet(directory / "pipes.parquet", index=False)
        self.nodes.to_parquet(directory / "nodes.parquet", index=False)
        _write_json(directory / "metadata.json", self._metadata_for_write())
        _write_json(directory / "profiles.json", self.profiles)
        logger.info("Wrote bundle %s to %s", self.name, directory)
        return directory

    def write(self, path: str | Path, format: str | None = None) -> Path:
        """
        Write the bundle as ``gpkg``, ``geojson`` or ``parquet``.

        ``format=None`` writes a GeoPackage when ``path`` ends in ``.gpkg``.
        ``geojson`` and ``parquet`` write a directory.

        Raises
        ------
        ValueError
            If the format is unknown or cannot be inferred.
        """
        path = Path(path)
        fmt = _resolve_format(path, format)
        if fmt == "gpkg":
            return self.to_gpkg(path)
        if fmt == "geojson":
            return self.to_geojson(path)
        return self.to_parquet(path)

    @classmethod
    def read(cls, path: str | Path, format: str | None = None) -> NetworkBundle:
        """
        Read a bundle written by :meth:`write`.

        Without ``format``, a ``.gpkg`` file is read as GeoPackage and a
        directory with ``pipes.parquet`` or ``pipes.geojson`` as GeoParquet or
        GeoJSON. GeoJSON is reprojected back to the source CRS.

        Raises
        ------
        ValueError
            If the format is ambiguous, ``schema_version`` is missing or has
            another major version, columns are missing or the metadata counts
            differ from the row counts.
        FileNotFoundError
            If the path or a required file does not exist.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        if format is None:
            if path.is_file() and path.suffix.lower() == ".gpkg":
                format = "gpkg"
            elif path.is_dir():
                found = [
                    f
                    for f, name in (
                        ("parquet", "pipes.parquet"),
                        ("geojson", "pipes.geojson"),
                    )
                    if (path / name).exists()
                ]
                if len(found) != 1:
                    raise ValueError(
                        f"Cannot infer the format of {path} "
                        f"({'both' if found else 'no'} pipes.parquet / pipes.geojson); "
                        "pass format=."
                    )
                format = found[0]
            else:
                raise ValueError(f"Cannot infer the format of {path}; pass format=.")
        elif format not in FORMATS:
            raise ValueError(f"Unknown format {format!r}; use one of {FORMATS}.")
        reader = {
            "gpkg": cls._read_gpkg,
            "geojson": cls._read_geojson,
            "parquet": cls._read_parquet,
        }[format]
        return reader(path)

    @classmethod
    def _assemble(
        cls,
        pipes: gpd.GeoDataFrame,
        nodes: gpd.GeoDataFrame,
        meta: dict,
        profiles: dict,
    ) -> NetworkBundle:
        pipes = _conform(pipes, PIPE_COLUMNS, PIPE_DTYPES, "pipes")
        nodes = _conform(nodes, NODE_COLUMNS, NODE_DTYPES, "nodes")
        counts = meta.get("counts")
        if counts is not None and (
            counts.get("pipes") != len(pipes) or counts.get("nodes") != len(nodes)
        ):
            raise ValueError(
                f"Metadata counts {counts} do not match the tables "
                f"({len(pipes)} pipes, {len(nodes)} nodes)."
            )
        return cls(pipes=pipes, nodes=nodes, metadata=meta, profiles=profiles)

    @classmethod
    def _read_gpkg(cls, path: Path) -> NetworkBundle:
        import fiona

        layers = fiona.listlayers(str(path))
        absent = [n for n in ("pipes", "nodes", _METADATA_LAYER) if n not in layers]
        if absent:
            raise ValueError(f"{path} lacks the layers {absent}.")
        with fiona.open(str(path), layer=_METADATA_LAYER) as src:
            rows = {f["properties"]["key"]: f["properties"]["value"] for f in src}
        if "metadata" not in rows:
            raise ValueError(f"{path} has no 'metadata' row in {_METADATA_LAYER}.")
        meta = json.loads(rows["metadata"])
        _check_schema_version(meta)
        profiles = _nan_profiles(json.loads(rows.get("profiles") or "{}"))
        pipes = gpd.read_file(path, layer="pipes")
        nodes = gpd.read_file(path, layer="nodes")
        return cls._assemble(pipes, nodes, meta, profiles)

    @staticmethod
    def _read_json_files(directory: Path) -> tuple[dict, dict]:
        meta_file, prof_file = directory / "metadata.json", directory / "profiles.json"
        if not meta_file.exists():
            raise FileNotFoundError(meta_file)
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        _check_schema_version(meta)
        profiles = {}
        if prof_file.exists():
            profiles = _nan_profiles(json.loads(prof_file.read_text(encoding="utf-8")))
        return meta, profiles

    @classmethod
    def _read_parquet(cls, directory: Path) -> NetworkBundle:
        meta, profiles = cls._read_json_files(directory)
        pipes = gpd.read_parquet(directory / "pipes.parquet")
        nodes = gpd.read_parquet(directory / "nodes.parquet")
        return cls._assemble(pipes, nodes, meta, profiles)

    @classmethod
    def _read_geojson(cls, directory: Path) -> NetworkBundle:
        meta, profiles = cls._read_json_files(directory)
        pipes = gpd.read_file(directory / "pipes.geojson")
        nodes = gpd.read_file(directory / "nodes.geojson")
        source = meta.get("source_crs_wkt")
        if source:
            crs = CRS.from_wkt(source)
            pipes, nodes = pipes.to_crs(crs), nodes.to_crs(crs)
            meta = {k: v for k, v in meta.items() if k != "source_crs_wkt"}
            meta["crs_wkt"] = crs.to_wkt()
            meta["crs_epsg"] = crs.to_epsg()
        return cls._assemble(pipes, nodes, meta, profiles)
