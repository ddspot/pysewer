# SPDX-FileCopyrightText: 2025 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""
Compare several designed sewer networks (variants) side by side.

:func:`compare` takes named bundles (or designed graphs) and returns a
:class:`Comparison` with tables for metrics, configuration differences,
diameters, stations and a spatial difference of the pipes.

Pipe IDs are per bundle and are **not** stable across variants. Pipes are
therefore matched by their end coordinates. ``tolerance`` is the maximum
distance (in the unit of the CRS, normally metres) between the end points of
two pipes for them to match: both end points must be within ``tolerance``
(Euclidean). Matching is one to one. The direction is ignored, so a pipe that
flows the other way in a variant still matches (it is then ``changed`` with
``flow_reversed=True``). If several pipes fit (parallel pipes), the closest
wins; ties go to equal diameter, then equal pipe type, then the lowest
``pipe_id``.

Notes
-----
A graph is converted to a bundle at the time of the call, and the bundle
stores the *active* configuration. To compare configurations, convert each
graph to a bundle (``NetworkBundle.from_graph``) while its configuration is
active, then pass the bundles.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from shapely.geometry import mapping

from pysewer.bundle import NetworkBundle
from pysewer.metrics import network_metrics

COMPARE_SCHEMA = "compare/1"

STATION_ROWS = {
    "pumping_stations": "n_pumping_stations",
    "lifting_stations": "n_lifting_stations",
    "private_pumps": "n_private_pumps",
}

_DIAMETER_ATOL = 1e-6


def _clean(value: Any) -> Any:
    """Return ``value`` as a JSON-safe Python object (NaN and inf become None)."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
        return f if math.isfinite(f) else None
    return value if isinstance(value, str) else str(value)


def _frame_to_dict(df: pd.DataFrame) -> dict:
    """Return a DataFrame as ``{"index", "columns", "data"}`` (JSON-able)."""
    return {
        "index": [_clean(i) for i in df.index],
        "columns": [_clean(c) for c in df.columns],
        "data": [[_clean(v) for v in row] for row in df.to_numpy(dtype=object)],
    }


def _to_geojson(gdf: gpd.GeoDataFrame) -> dict:
    """Return a GeoDataFrame as a WGS84 GeoJSON FeatureCollection (lon, lat)."""
    if gdf.crs is None:
        raise ValueError("A GeoDataFrame without a CRS cannot be converted to WGS84.")
    wgs = gdf.to_crs(4326)  # geopandas uses lon/lat order (always_xy)
    cols = [c for c in wgs.columns if c != wgs.geometry.name]
    features = []
    for geom, row in zip(wgs.geometry, wgs[cols].to_dict("records"), strict=True):
        features.append(
            {
                "type": "Feature",
                "geometry": None if geom is None or geom.is_empty else mapping(geom),
                "properties": {k: _clean(v) for k, v in row.items()},
            }
        )
    return _clean_geometry({"type": "FeatureCollection", "features": features})


def _clean_geometry(fc: dict) -> dict:
    """Turn coordinate tuples into lists of Python floats."""
    for f in fc["features"]:
        g = f["geometry"]
        if g is not None:
            g["coordinates"] = _clean(g["coordinates"])
    return fc


@dataclass(frozen=True, eq=False)
class Comparison:
    """
    Result of :func:`compare`.

    Attributes
    ----------
    base : str
        Name of the base variant.
    names : tuple of str
        Variant names in input order.
    tolerance : float
        Maximum end point distance (CRS units) for two pipes to match.
    metrics : pandas.DataFrame
        Scalar metrics of :func:`pysewer.metrics.network_metrics`; rows are
        metric keys, columns are variants (undefined values are NaN).
    metrics_delta : pandas.DataFrame
        Same shape as ``metrics``: delta = variant - base (base column is 0).
    config_diff : pandas.DataFrame
        Dotted configuration keys whose value differs between variants.
        Lists and dicts are shown as JSON strings, a missing key as NaN
        (null in :meth:`to_dict`).
    diameters : pandas.DataFrame
        Rows are diameters (m), columns are variants, values are pipe length
        in m (gravity and pressurized).
    diameter_counts : pandas.DataFrame
        Same as ``diameters`` with the number of pipes.
    stations : pandas.DataFrame
        Rows ``pumping_stations``, ``lifting_stations``, ``private_pumps``.
    spatial : pandas.DataFrame
        Per variant against the base, lengths in m: ``shared_length_m``
        (matched pipes, base length), ``shared_length_variant_m`` (the same
        pipes, variant length), ``added_length_m``, ``removed_length_m``,
        ``changed_diameter_length_m``, ``changed_type_length_m`` (base
        length of shared pipes), ``n_shared``, ``n_added``, ``n_removed``,
        ``n_changed``. ``shared + removed`` is the base total, and
        ``shared_length_variant_m + added`` is the variant total. The base
        row has its whole length as shared and zero elsewhere.
        ``n_changed`` is a subset of ``n_shared``: unchanged pipes are
        ``n_shared - n_changed``. The two changed lengths can overlap, and a
        pipe whose only change is the flow direction counts in ``n_changed``
        but in neither changed length.
    pipe_diff : dict of str to geopandas.GeoDataFrame
        Per non-base variant, one row per pipe with ``status`` (``shared``,
        ``added``, ``removed``, ``changed``), ``flow_reversed`` and the
        ``diameter_*`` and ``pipe_type_*`` columns for base and variant.
        Geometry is from the variant, or from the base for removed pipes.
    """

    base: str
    names: tuple
    tolerance: float
    metrics: pd.DataFrame
    metrics_delta: pd.DataFrame
    config_diff: pd.DataFrame
    diameters: pd.DataFrame
    diameter_counts: pd.DataFrame
    stations: pd.DataFrame
    spatial: pd.DataFrame
    pipe_diff: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """
        Return the comparison as a JSON-able dictionary.

        Tables become ``{"index", "columns", "data"}``; each ``pipe_diff``
        becomes a GeoJSON FeatureCollection in WGS84 (lon, lat order).
        NaN and infinity become None; numbers are Python scalars.

        Returns
        -------
        dict
            Safe for ``json.dumps(..., allow_nan=False)``.
        """
        return {
            "schema": COMPARE_SCHEMA,
            "base": self.base,
            "names": list(self.names),
            "tolerance": float(self.tolerance),
            "metrics": _frame_to_dict(self.metrics),
            "metrics_delta": _frame_to_dict(self.metrics_delta),
            "config_diff": _frame_to_dict(self.config_diff),
            "diameters": _frame_to_dict(self.diameters),
            "diameter_counts": _frame_to_dict(self.diameter_counts),
            "stations": _frame_to_dict(self.stations),
            "spatial": _frame_to_dict(self.spatial),
            "pipe_diff": {k: _to_geojson(v) for k, v in self.pipe_diff.items()},
        }


def _flatten(d: Mapping, prefix: str = "") -> dict:
    """Flatten nested dicts to dotted keys (lists stay values)."""
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, Mapping) and v:
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def _show(value: Any) -> Any:
    """Show lists and dicts as JSON strings."""
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, sort_keys=True, default=str)
    return value


def _config_diff(bundles: dict[str, NetworkBundle]) -> pd.DataFrame:
    flat = {
        n: _flatten((b.metadata or {}).get("config") or {}) for n, b in bundles.items()
    }
    keys = sorted({k for f in flat.values() for k in f})
    rows = {}
    for k in keys:
        vals = [_show(f.get(k)) for f in flat.values()]
        if len({json.dumps(v, default=str) for v in vals}) > 1:
            rows[k] = vals
    df = pd.DataFrame.from_dict(rows, orient="index", columns=list(bundles))
    df.index.name = "config_key"
    return df


def _scalar_metrics(m: dict) -> dict:
    out = {}
    for k, v in m.items():
        if isinstance(v, (dict, str)):
            continue
        out[k] = np.nan if v is None else float(v)
    return out


def _dict_table(dicts: dict[str, dict]) -> pd.DataFrame:
    """Table with float-key rows from ``{variant: {str(diameter): value}}``."""
    keys = sorted({float(k) for d in dicts.values() for k in d})
    df = pd.DataFrame(0.0, index=pd.Index(keys, name="diameter_m"), columns=list(dicts))
    for n, d in dicts.items():
        for k, v in d.items():
            df.loc[float(k), n] = float(v)
    return df


def _idkey(value: Any) -> tuple:
    """Sort key that also works for mixed numeric and text pipe IDs."""
    if isinstance(value, (int, float, np.integer, np.floating)) and not (
        isinstance(value, float) and math.isnan(value)
    ):
        return (0, float(value), "")
    return (1, 0.0, str(value))


def _records(pipes: gpd.GeoDataFrame) -> list[dict]:
    """One record per pipe with its two end points."""
    recs = []
    for geom, row in zip(
        pipes.geometry,
        pipes[["pipe_id", "length", "diameter", "pipe_type"]].itertuples(index=False),
        strict=True,
    ):
        coords = list(geom.coords)
        recs.append(
            {
                "p0": (float(coords[0][0]), float(coords[0][1])),
                "p1": (float(coords[-1][0]), float(coords[-1][1])),
                "pipe_id": row.pipe_id,
                "length": float(row.length),
                "diameter": float(row.diameter) if pd.notna(row.diameter) else np.nan,
                "pipe_type": row.pipe_type,
                "geometry": geom,
            }
        )
    return recs


def _cell(p: tuple[float, float], tol: float) -> tuple[int, int]:
    return (math.floor(p[0] / tol), math.floor(p[1] / tol))


def _match(
    base: list[dict], var: list[dict], tol: float
) -> dict[int, tuple[int, bool]]:
    """Match variant pipes to base pipes, one to one.

    Two pipes match if both end points lie within ``tol`` (Euclidean) of the
    end points of the other pipe, in the same or in the swapped order.
    Candidates come from a grid index (cell size ``tol``, 3x3 neighbourhood),
    so the cost is O(n) on average. All candidate pairs are ranked by the sum
    of the end point distances; ties go to equal diameter, then equal
    pipe type, then the lowest base and variant pipe_id. Pairs are taken in
    that order, each pipe once, so the result does not depend on row order.

    Returns ``{variant index: (base index, forward)}``; ``forward`` is False
    when the variant pipe runs the other way.
    """
    index: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, r in enumerate(base):
        index[_cell(r["p0"], tol)].append(i)
        c1 = _cell(r["p1"], tol)
        if c1 != _cell(r["p0"], tol):
            index[c1].append(i)

    pairs = []
    for j, v in enumerate(var):
        cx, cy = _cell(v["p0"], tol)
        cands = {
            i
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for i in index.get((cx + dx, cy + dy), ())
        }
        for i in cands:
            b = base[i]
            fwd = (
                math.hypot(b["p0"][0] - v["p0"][0], b["p0"][1] - v["p0"][1]),
                (math.hypot(b["p1"][0] - v["p1"][0], b["p1"][1] - v["p1"][1])),
            )
            rev = (
                math.hypot(b["p0"][0] - v["p1"][0], b["p0"][1] - v["p1"][1]),
                (math.hypot(b["p1"][0] - v["p0"][0], b["p1"][1] - v["p0"][1])),
            )
            options = []
            if max(fwd) <= tol:
                options.append((sum(fwd), 0, True))
            if max(rev) <= tol:
                options.append((sum(rev), 1, False))
            if not options:
                continue
            dist, _, forward = min(options)
            pairs.append(
                (
                    dist,
                    not _same_diameter(b["diameter"], v["diameter"]),
                    b["pipe_type"] != v["pipe_type"],
                    _idkey(b["pipe_id"]),
                    _idkey(v["pipe_id"]),
                    i,
                    j,
                    forward,
                )
            )
    pairs.sort()
    used_b: set[int] = set()
    matched: dict[int, tuple[int, bool]] = {}
    for *_, i, j, forward in pairs:
        if i in used_b or j in matched:
            continue
        used_b.add(i)
        matched[j] = (i, forward)
    return matched


def _same_diameter(a: float, b: float) -> bool:
    if math.isnan(a) and math.isnan(b):
        return True
    if math.isnan(a) or math.isnan(b):
        return False
    return abs(a - b) <= _DIAMETER_ATOL


def _diff(
    base: NetworkBundle, var: NetworkBundle, tol: float
) -> tuple[gpd.GeoDataFrame, dict]:
    rb = _records(base.pipes)
    rv = _records(var.pipes)
    matched = _match(rb, rv, tol)
    base_to_var = {i: (j, fwd) for j, (i, fwd) in matched.items()}
    entries = [
        (rb[i], rv[base_to_var[i][0]] if i in base_to_var else None)
        for i in range(len(rb))
    ]
    entries += [(None, rv[j]) for j in range(len(rv)) if j not in matched]
    flips = {i: not fwd for i, (_, fwd) in base_to_var.items()}
    rows = []
    sums: dict = defaultdict(float)
    counts: dict = defaultdict(int)
    for n, (b, v) in enumerate(entries):
        reversed_ = False
        if b is not None and v is not None:
            same_d = _same_diameter(b["diameter"], v["diameter"])
            same_t = b["pipe_type"] == v["pipe_type"]
            reversed_ = bool(flips[n])
            status = "shared" if (same_d and same_t and not reversed_) else "changed"
            sums["shared"] += b["length"]
            sums["shared_variant"] += v["length"]
            counts["shared"] += 1
            if status == "changed":
                counts["changed"] += 1
            if not same_d:
                sums["diameter"] += b["length"]
            if not same_t:
                sums["type"] += b["length"]
            geom = v["geometry"]
        elif v is not None:
            status, geom = "added", v["geometry"]
            sums["added"] += v["length"]
            counts["added"] += 1
        else:
            status, geom = "removed", b["geometry"]
            sums["removed"] += b["length"]
            counts["removed"] += 1

        def get(r, name):
            return None if r is None else r[name]

        def num(r, name):
            return np.nan if r is None else r[name]

        rows.append(
            {
                "status": status,
                "pipe_id_base": get(b, "pipe_id"),
                "pipe_id_variant": get(v, "pipe_id"),
                "length_base": num(b, "length"),
                "length_variant": num(v, "length"),
                "diameter_base": num(b, "diameter"),
                "diameter_variant": num(v, "diameter"),
                "pipe_type_base": get(b, "pipe_type"),
                "pipe_type_variant": get(v, "pipe_type"),
                "flow_reversed": reversed_,
                "geometry": geom,
            }
        )
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=base.crs)
    spatial = {
        "shared_length_m": sums["shared"],
        "shared_length_variant_m": sums["shared_variant"],
        "added_length_m": sums["added"],
        "removed_length_m": sums["removed"],
        "changed_diameter_length_m": sums["diameter"],
        "changed_type_length_m": sums["type"],
        "n_shared": counts["shared"],
        "n_added": counts["added"],
        "n_removed": counts["removed"],
        "n_changed": counts["changed"],
    }
    return gdf, spatial


def compare(
    bundles: Mapping[str, NetworkBundle | nx.Graph],
    *,
    base: str | None = None,
    tolerance: float = 0.01,
) -> Comparison:
    """
    Compare named network variants.

    Parameters
    ----------
    bundles : mapping of str to NetworkBundle or networkx.Graph
        Variants by name (at least one). Graphs are converted with
        :meth:`NetworkBundle.from_graph` (the active configuration is
        stored, see the module notes).
    base : str, optional
        Name of the base variant. Default: the first key.
    tolerance : float, default 0.01
        Maximum distance in CRS units (metres for a projected CRS) between
        the end points of two pipes for them to match. Ties between parallel
        pipes go to equal diameter, equal type, then lowest pipe_id.

    Returns
    -------
    Comparison

    Raises
    ------
    ValueError
        If ``bundles`` is empty, ``base`` is unknown, ``tolerance`` is not
        positive, or the variants do not share one CRS.
    """
    if not bundles:
        raise ValueError("compare() needs at least one bundle.")
    if tolerance <= 0:
        raise ValueError("tolerance must be positive.")
    bs: dict[str, NetworkBundle] = {
        str(n): (b if isinstance(b, NetworkBundle) else NetworkBundle.from_graph(b))
        for n, b in bundles.items()
    }
    names = list(bs)
    base = names[0] if base is None else base
    if base not in bs:
        raise ValueError(f"Unknown base {base!r}; choose one of {names}.")
    crs0 = bs[base].crs
    for n, b in bs.items():
        if b.crs != crs0:
            raise ValueError(
                f"All variants must share one CRS; {n!r} differs from {base!r}."
            )

    mets = {n: network_metrics(b) for n, b in bs.items()}
    metrics = pd.DataFrame({n: _scalar_metrics(m) for n, m in mets.items()})
    metrics.index.name = "metric"
    delta = metrics.sub(metrics[base], axis=0)
    diameters = _dict_table({n: m["diameter_length_m"] for n, m in mets.items()})
    counts = _dict_table({n: m["diameter_count"] for n, m in mets.items()})
    stations = pd.DataFrame(
        {n: {r: int(m[k]) for r, k in STATION_ROWS.items()} for n, m in mets.items()}
    )

    spatial_rows, pipe_diff = {}, {}
    for n, b in bs.items():
        if n == base:
            tot = float(b.pipes["length"].sum())
            spatial_rows[n] = {
                "shared_length_m": tot,
                "shared_length_variant_m": tot,
                "added_length_m": 0.0,
                "removed_length_m": 0.0,
                "changed_diameter_length_m": 0.0,
                "changed_type_length_m": 0.0,
                "n_shared": len(b.pipes),
                "n_added": 0,
                "n_removed": 0,
                "n_changed": 0,
            }
        else:
            pipe_diff[n], spatial_rows[n] = _diff(bs[base], b, tolerance)
    spatial = pd.DataFrame.from_dict(spatial_rows, orient="index").loc[names]
    spatial.index.name = "variant"

    return Comparison(
        base=base,
        names=tuple(names),
        tolerance=float(tolerance),
        metrics=metrics,
        metrics_delta=delta,
        config_diff=_config_diff(bs),
        diameters=diameters,
        diameter_counts=counts,
        stations=stations,
        spatial=spatial,
        pipe_diff=pipe_diff,
    )
