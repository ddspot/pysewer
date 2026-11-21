# SPDX-License-Identifier: GPL-3.0-only
"""
Fetch the input data of a run for any place on Earth.

:func:`fetch_inputs` downloads OpenStreetMap roads and buildings and a DEM for
a WGS84 bounding box, reprojects everything to a projected CRS and caches the
results on disk. It returns :class:`~pysewer.pipeline.Inputs` that
:func:`~pysewer.pipeline.generate` accepts directly. A host (for example the
plugin) can fetch in one command and later call :func:`fetch_inputs` with
``offline=True`` to read from the cache only.

Two vector sources exist. ``source="osm"`` (default) queries an Overpass
server through ``osmnx``; the server is configurable with ``overpass_url``.
``source="overture"`` reads the public Overture Maps GeoParquet on AWS S3
through the ``overturemaps`` package and needs no Overpass server.

The module needs the optional ``fetch`` extra (``osmnx``, ``overturemaps``).
Heavy imports happen inside the functions, so ``import pysewer.sources``
stays light.

Data notes
----------
* The default DEM is Copernicus GLO-30. It is a digital *surface* model:
  buildings and trees raise the ground in towns. Its resolution is 30 m, so
  slopes of short pipes are noisy. Use a local bare-earth DEM (for example a
  1 m LiDAR DGM) with ``dem_source=<path>`` where one exists.
* Roads and buildings are (c) OpenStreetMap contributors, ODbL. Overture
  buildings and roads are ODbL too and carry extra attributions of their
  upstream sources (see :data:`OVERTURE_ATTRIBUTION`).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import os
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np

from .pipeline import Inputs

__all__ = [
    "COPERNICUS_ATTRIBUTION",
    "COPERNICUS_DERIVED_ATTRIBUTION",
    "COPERNICUS_URL_TEMPLATE",
    "DEFAULT_HIGHWAY",
    "DEM_SOURCES",
    "OSM_ATTRIBUTION",
    "OVERTURE_ATTRIBUTION",
    "SOURCES",
    "AreaTooLargeError",
    "FetchError",
    "OfflineCacheMissError",
    "fetch_dem",
    "fetch_inputs",
    "preflight_building_count",
]

logger = logging.getLogger(__name__)
Progress = Callable[[str, float, str], None]


class FetchError(ValueError):
    """Input data could not be fetched or is unusable (for example empty)."""


class AreaTooLargeError(FetchError):
    """The bounding box is larger than ``max_area_km2``."""


class OfflineCacheMissError(FetchError, FileNotFoundError):
    """``offline=True`` was set and the cache has no data for this area."""


#: OSM highway classes kept as sewer corridors: public streets and links.
#: ``service`` is kept except ``service=parking_aisle`` and ``service=driveway``
#: (the filter drops those two). Motorways, trunks, tracks, paths and footways
#: are not kept.
DEFAULT_HIGHWAY: tuple[str, ...] = (
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "living_street",
    "road",
    "primary_link",
    "secondary_link",
    "tertiary_link",
    "service",
)
_SERVICE_EXCLUDE = ("parking_aisle", "driveway")

COPERNICUS_URL_TEMPLATE = (
    "https://copernicus-dem-30m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
    "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif"
)
COPERNICUS_ATTRIBUTION = (
    "© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 "
    "provided under COPERNICUS by the European Union and ESA; all rights reserved"
)
#: For the cached DEM, which is resampled and so a derived product.
COPERNICUS_DERIVED_ATTRIBUTION = (
    "produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and "
    "© Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the "
    "European Union and ESA; all rights reserved"
)
OSM_ATTRIBUTION = (
    "© OpenStreetMap contributors (ODbL, https://www.openstreetmap.org/copyright)"
)

#: Overture attribution. The Overture docs (docs.overturemaps.org/attribution)
#: say: "Some of the data sources we use in Overture datasets require their own
#: attribution, according to their licenses [...] we added the following line
#: of attribution to adhere to the requirements of the Open Database License:
#: (c) OpenStreetMap contributors, Overture Maps Foundation." The licence of
#: both the buildings and the transportation theme is ODbL. The upstream
#: sources add notes: Microsoft Global ML Building Footprints (ODbL), the City
#: of Vancouver (Open Government Licence - Vancouver) and TomTom.
OVERTURE_ATTRIBUTION = (
    "© OpenStreetMap contributors, Overture Maps Foundation (ODbL, "
    "https://docs.overturemaps.org/attribution/)"
)
#: Vector sources of :func:`fetch_inputs`.
SOURCES: tuple[str, ...] = ("osm", "overture")
#: Environment variable that sets the Overpass server when no argument is given.
OVERPASS_URL_ENV = "PYSEWER_OVERPASS_URL"
#: Overture buildings with a smaller footprint (m2, in the target CRS) are dropped.
OVERTURE_MIN_BUILDING_AREA_M2 = 20.0
_OVERTURE_S3_URL = "https://overturemaps-us-west-2.s3.amazonaws.com/"
_OVERTURE_TIMEOUT_S = 60
_OVERTURE_RELEASE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(\.\d+)?(-[A-Za-z0-9.]+)?$")

_DEM_RES_M = 30.0
_DEM_MARGIN_M = 200.0
_NODATA = -9999.0
_PASS_THROUGH_FACTOR = 4.0
_NETWORK_MARKERS = (
    "curl error",
    "could not resolve host",
    "timed out",
    "timeout",
    "connection refused",
    "connection reset",
    "network is unreachable",
)


def _default_cache_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / "pysewer" / "sources"
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "pysewer" / "sources"


def _report(progress: Progress | None, stage: str, fraction: float, msg: str) -> None:
    logger.info("[%s] %s", stage, msg)
    if progress is not None:
        progress(stage, fraction, msg)


def _validate_bbox(bbox, max_area_km2: float) -> tuple[float, float, float, float]:
    try:
        w, s, e, n = (float(v) for v in bbox)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "bbox_wgs84 must be four numbers (west, south, east, north)."
        ) from exc
    if not all(math.isfinite(v) for v in (w, s, e, n)):
        raise ValueError("bbox_wgs84 contains non-finite values.")
    if not (-180 <= w < e <= 180):
        raise ValueError(
            f"bbox_wgs84 needs -180 <= west < east <= 180, got west={w}, east={e}."
        )
    if not (-90 <= s < n <= 90):
        raise ValueError(
            f"bbox_wgs84 needs -90 <= south < north <= 90, got south={s}, north={n}."
        )
    from pyproj import Geod
    from shapely.geometry import box

    area, _ = Geod(ellps="WGS84").geometry_area_perimeter(box(w, s, e, n))
    area_km2 = abs(area) / 1e6
    if area_km2 > max_area_km2:
        raise AreaTooLargeError(
            f"The bounding box covers {area_km2:.2f} km2, more than the limit of "
            f"{max_area_km2:g} km2. Use a smaller box or raise max_area_km2."
        )
    return w, s, e, n


def _round_bbox(bbox) -> list[float]:
    return [round(v, 5) for v in bbox]


def _key(*parts) -> str:
    blob = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _margins(bbox, factor: float = 1.0) -> tuple[float, float]:
    """Margin ``(dlon, dlat)`` in degrees of ``factor`` times the DEM margin."""
    _, s, _, n = bbox
    dlat = factor * _DEM_MARGIN_M / 111_320.0
    dlon = dlat / max(math.cos(math.radians((s + n) / 2)), 0.01)
    return dlon, dlat


# --------------------------------------------------------------------------
# Retry and osmnx settings
# --------------------------------------------------------------------------


_STATUS_RE = re.compile(r"\b([1-5]\d\d)\b")


def _is_transient(exc: Exception) -> bool:
    """True for HTTP 429/5xx, connection errors and timeouts; false for 4xx."""
    if type(exc).__name__ == "ResponseStatusCodeError":
        # osmnx puts the status in the message ("... responded: 502 Bad ...").
        m = _STATUS_RE.search(str(exc))
        return m is None or int(m.group(1)) >= 500 or int(m.group(1)) == 429
    try:
        import requests
    except ImportError:  # pragma: no cover
        return False
    if isinstance(exc, requests.exceptions.HTTPError):
        code = exc.response.status_code if exc.response is not None else 500
        return code >= 500 or code == 429
    return isinstance(
        exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
    )


def _with_retry(call, retries: int, retry_wait: float):
    """
    Call ``call()``; retry on Overpass server errors and connection errors.

    The waits double (``retry_wait``, ``2 * retry_wait``, ...) but the total
    sleep time never exceeds ``retries * retry_wait``.
    """
    slept = 0.0
    budget = retries * retry_wait
    for i in range(retries + 1):
        try:
            return call()
        except Exception as exc:
            if not _is_transient(exc) or i == retries:
                raise
            delay = min(retry_wait * 2**i, budget - slept)
            logger.warning("OSM request failed (%s); retrying in %.1f s", exc, delay)
            time.sleep(delay)
            slept += delay


def normalise_overpass_url(url: str) -> str:
    """
    Return the Overpass base URL in the form ``osmnx.settings.overpass_url`` has.

    osmnx 2.x appends ``/interpreter`` itself, so ``.../api/interpreter`` and
    ``.../api/`` both become ``.../api``.

    Raises
    ------
    ValueError
        If the value is not an ``http://`` or ``https://`` URL.
    """
    text = str(url).strip().rstrip("/")
    if not re.match(r"^https?://[^/\s]+", text):
        raise ValueError(
            f"overpass_url must be an http(s) URL such as "
            f"https://overpass-api.de/api, got {url!r}."
        )
    if text.endswith("/interpreter"):
        text = text[: -len("/interpreter")]
    return text


def resolve_overpass_url(overpass_url: str | None = None) -> str | None:
    """
    Pick the Overpass server: argument, then ``PYSEWER_OVERPASS_URL``, then None.

    ``None`` means: keep the osmnx default. A set value is normalised with
    :func:`normalise_overpass_url`. An empty string counts as not set.
    """
    value = overpass_url or os.environ.get(OVERPASS_URL_ENV) or None
    return normalise_overpass_url(value) if value else None


def _effective_overpass_url(overpass_url: str | None) -> str | None:
    """The resolved URL, or the osmnx default (None if osmnx is missing)."""
    resolved = resolve_overpass_url(overpass_url)
    if resolved is not None:
        return resolved
    try:
        import osmnx as ox

        return normalise_overpass_url(ox.settings.overpass_url)
    except Exception:
        return None


@contextlib.contextmanager
def _osmnx_cache(ox, cache_dir: Path, overpass_url: str | None = None):
    """
    Point osmnx's HTTP cache into ``cache_dir`` (and optionally its Overpass
    server to ``overpass_url``) and restore both afterwards.
    """
    old = (ox.settings.cache_folder, ox.settings.use_cache, ox.settings.overpass_url)
    ox.settings.cache_folder = str(cache_dir / "osmnx")
    ox.settings.use_cache = True
    if overpass_url:
        ox.settings.overpass_url = overpass_url
    try:
        yield
    finally:
        (
            ox.settings.cache_folder,
            ox.settings.use_cache,
            ox.settings.overpass_url,
        ) = old


def _is_empty_error(exc: Exception) -> bool:
    name = type(exc).__name__
    return "nsufficient" in name or "mpty" in name


# --------------------------------------------------------------------------
# Cache helpers
# --------------------------------------------------------------------------


def _cached_gpkg(path: Path, offline: bool, what: str) -> gpd.GeoDataFrame | None:
    if path.exists():
        logger.info("Using cached %s: %s", what, path)
        return gpd.read_file(path)
    if offline:
        raise OfflineCacheMissError(
            f"offline=True but no cached {what} for this area (expected {path})."
        )
    return None


def _write_gpkg_atomic(gdf: gpd.GeoDataFrame, path: Path) -> None:
    tmp = path.with_name(f"{path.stem}.part{os.getpid()}.gpkg")
    try:
        gdf.to_file(tmp, driver="GPKG")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _join(value) -> str:
    if isinstance(value, (list, tuple, set)):
        return ";".join(str(v) for v in value)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value)


def _highway_filters(highway) -> list[str]:
    classes = [h for h in highway if h != "service"]
    filters = []
    if classes:
        filters.append('["highway"~"^({})$"]'.format("|".join(classes)))
    if "service" in highway:
        filters.append(
            '["highway"="service"]["service"!~"^({})$"]'.format(
                "|".join(_SERVICE_EXCLUDE)
            )
        )
    return filters


# --------------------------------------------------------------------------
# OSM
# --------------------------------------------------------------------------


def _roads_cache_path(cache_dir: Path, bbox, highway) -> Path:
    key = _key(_round_bbox(bbox), sorted(highway), list(_SERVICE_EXCLUDE))
    return cache_dir / f"roads_{key}.gpkg"


def _buildings_cache_path(cache_dir: Path, bbox) -> Path:
    return cache_dir / f"buildings_{_key(_round_bbox(bbox))}.gpkg"


def _fetch_roads(
    bbox, highway, cache_dir, offline, retries, retry_wait, overpass_url=None
):
    path = _roads_cache_path(cache_dir, bbox, highway)
    cached = _cached_gpkg(path, offline, "roads")
    if cached is not None:
        return cached, True
    import osmnx as ox

    try:
        with _osmnx_cache(ox, cache_dir, overpass_url):
            graph = _with_retry(
                lambda: ox.graph_from_bbox(
                    tuple(bbox),
                    custom_filter=_highway_filters(highway),
                    simplify=True,
                    retain_all=True,
                ),
                retries,
                retry_wait,
            )
        edges = ox.graph_to_gdfs(ox.convert.to_undirected(graph), nodes=False)
    except Exception as exc:  # osmnx raises several "empty" error types
        if _is_empty_error(exc):
            raise FetchError("OSM has no roads in this area") from exc
        raise
    edges = edges.reset_index(drop=True)
    if edges.empty:
        raise FetchError("OSM has no roads in this area")
    name = edges["name"] if "name" in edges else [None] * len(edges)
    out = gpd.GeoDataFrame(
        {
            "osmid": [_join(v) for v in edges["osmid"]],
            "highway": [_join(v) for v in edges["highway"]],
            "name": [_join(v) for v in name],
        },
        geometry=edges.geometry.values,
        crs=edges.crs,
    ).to_crs(4326)
    _write_gpkg_atomic(out, path)
    return out, False


def _fetch_buildings(bbox, cache_dir, offline, retries, retry_wait, overpass_url=None):
    path = _buildings_cache_path(cache_dir, bbox)
    cached = _cached_gpkg(path, offline, "buildings")
    if cached is not None:
        return cached, True
    import osmnx as ox

    try:
        with _osmnx_cache(ox, cache_dir, overpass_url):
            feats = _with_retry(
                lambda: ox.features_from_bbox(tuple(bbox), tags={"building": True}),
                retries,
                retry_wait,
            )
    except Exception as exc:
        if _is_empty_error(exc):
            raise FetchError("OSM has no buildings in this area") from exc
        raise
    feats = feats[feats.geom_type.isin(["Polygon", "MultiPolygon"])]
    if feats.empty:
        raise FetchError("OSM has no buildings in this area")
    ids = feats.index.get_level_values(-1)
    out = gpd.GeoDataFrame(
        {
            "osmid": np.asarray(ids).astype(str),
            "building": [_join(v) for v in feats["building"]],
        },
        geometry=feats.geometry.values,
        crs=feats.crs,
    ).to_crs(4326)
    _write_gpkg_atomic(out, path)
    return out, False


_REPO_URL = "https://codebase.helmholtz.cloud/wasp/pysewer"


def _http_headers() -> dict:
    """Headers for our own Overpass request (osmnx's, with a stdlib fallback)."""
    try:
        from importlib.metadata import version

        ver = version("pysewer")
    except Exception:
        ver = "unknown"
    ua = f"pysewer/{ver} (+{_REPO_URL})"
    try:
        from osmnx._http import _get_http_headers

        return dict(_get_http_headers(user_agent=ua))
    except (ImportError, AttributeError, TypeError):
        return {
            "User-Agent": ua,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Referer": _REPO_URL,
        }


def preflight_building_count(
    bbox_wgs84: tuple[float, float, float, float],
    *,
    timeout: float = 60.0,
    retries: int = 3,
    retry_wait: float = 5.0,
    overpass_url: str | None = None,
    source: str = "osm",
    overture_release: str | None = None,
) -> int | None:
    """
    Count the buildings in a box before a download.

    For ``source="osm"`` this is one cheap Overpass ``out count`` query. Use it
    before :func:`fetch_inputs` to warn about very large downloads.

    For ``source="overture"`` the count comes from the building footprints'
    ``bbox`` columns on S3. It counts rows that touch the box, before the
    underground and area filters. It returns ``None`` when the count fails (for
    example when the package ``overturemaps`` is missing or S3 is slow), so the
    caller can skip the warning.

    Parameters
    ----------
    bbox_wgs84 : tuple of float
        ``(west, south, east, north)`` in decimal degrees.
    timeout : float, default 60
        Request timeout in seconds (also the Overpass server timeout).
    retries, retry_wait : int, float
        Retry settings, see :func:`fetch_inputs`.
    overpass_url : str, optional
        Overpass server, see :func:`fetch_inputs`. Ignored for Overture.
    source : {"osm", "overture"}, default "osm"
        Data source.
    overture_release : str, optional
        Overture release. Default: the latest.

    Returns
    -------
    int or None
        Number of buildings in the box; ``None`` if an Overture count fails.
    """
    import requests

    w, s, e, n = _validate_bbox(bbox_wgs84, float("inf"))
    _check_source(source)
    if source == "overture":
        try:
            return _overture_count((w, s, e, n), overture_release, timeout)
        except Exception as exc:
            logger.warning("Overture building count failed: %s", exc)
            return None
    url = (_effective_overpass_url(overpass_url) or "").rstrip("/") + "/interpreter"
    box_ = f"({s},{w},{n},{e})"
    query = (
        f"[out:json][timeout:{int(timeout)}];"
        f'(way["building"]{box_};relation["building"]{box_};);out count;'
    )

    def call() -> int:
        resp = requests.post(
            url,
            data={"data": query},
            headers=_http_headers(),
            timeout=timeout,
        )
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
        return int(elements[0]["tags"]["total"]) if elements else 0

    return _with_retry(call, retries, retry_wait)


# --------------------------------------------------------------------------
# Overture Maps
# --------------------------------------------------------------------------

#: Overture ``transportation`` classes that map 1:1 to an OSM ``highway`` value.
_OVERTURE_ROAD_CLASSES = frozenset(
    {
        "primary",
        "secondary",
        "tertiary",
        "unclassified",
        "residential",
        "living_street",
    }
)
_OVERTURE_LINK_CLASSES = frozenset({"primary", "secondary", "tertiary"})
_OVERTURE_SERVICE_EXCLUDE = frozenset(_SERVICE_EXCLUDE)


def _check_source(source: str) -> None:
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; known: {', '.join(SOURCES)}")


def _overture_highway(road_class, subclass) -> str | None:
    """
    Map an Overture road ``class``/``subclass`` to a pysewer ``highway`` value.

    Returns ``None`` for roads that are no sewer corridors (motorways, trunks,
    footways, paths, steps, cycleways, pedestrian streets, tracks, driveways
    and parking aisles).
    """

    def clean(v):
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else v

    road_class, subclass = clean(road_class), clean(subclass)
    if road_class in _OVERTURE_ROAD_CLASSES:
        if subclass == "link" and road_class in _OVERTURE_LINK_CLASSES:
            return f"{road_class}_link"
        return str(road_class)
    if road_class == "service":
        return None if subclass in _OVERTURE_SERVICE_EXCLUDE else "service"
    if road_class in (None, "unknown"):
        return "road"
    return None


def _overture_name(names) -> str:
    """``names.primary`` of an Overture ``names`` struct, or an empty string."""
    if isinstance(names, dict):
        return _join(names.get("primary"))
    return ""


def _overture_import():
    try:
        from overturemaps import core
    except ImportError as exc:
        raise ImportError(
            "source='overture' needs the 'overturemaps' package. "
            "Install it with: uv pip install 'pysewer[fetch]'"
        ) from exc
    return core


def _overture_latest_release() -> str:
    """The latest Overture release, from the public STAC catalog."""
    core = _overture_import()
    try:
        return str(core.get_latest_release())
    except Exception as exc:
        raise ConnectionError(
            f"Could not read the Overture release catalog (stac.overturemaps.org): {exc}"
        ) from exc


def _overture_count(bbox, release, timeout: float) -> int:
    core = _overture_import()
    release = release or _overture_latest_release()
    return int(
        core.count_rows(
            "building",
            bbox,
            release,
            connect_timeout=int(timeout),
            request_timeout=int(timeout),
        )
    )


def _read_overture(overture_type: str, bbox, release: str) -> gpd.GeoDataFrame:
    """
    Read one Overture type for a box from S3 (a WGS84 GeoDataFrame).

    The reader filters the ``bbox`` struct columns, so only the files and row
    groups that touch the box are read. This is the one function that touches
    the network, so tests replace it.
    """
    core = _overture_import()
    try:
        reader = core.record_batch_reader(
            overture_type,
            tuple(bbox),
            release,
            connect_timeout=_OVERTURE_TIMEOUT_S,
            request_timeout=_OVERTURE_TIMEOUT_S,
        )
        if reader is None:
            raise ConnectionError("the Overture reader returned no data (see log)")
        gdf = gpd.GeoDataFrame.from_arrow(reader)
    except OSError as exc:  # pyarrow's S3 errors are OSError (ArrowIOError)
        raise ConnectionError(
            f"Could not read Overture Maps from S3 ({_OVERTURE_S3_URL}): {exc}"
        ) from exc
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    return gdf


def _within_box(gdf: gpd.GeoDataFrame, bbox) -> gpd.GeoDataFrame:
    from shapely.geometry import box

    return gdf[gdf.intersects(box(*bbox))]


def _clip_lines(gdf: gpd.GeoDataFrame, bbox) -> gpd.GeoDataFrame:
    """
    Cut lines at the box and keep the parts inside (one LineString per row).

    Overture segments can be kilometres long. Uncut, they would run beyond the
    DEM, which only covers the box plus a margin.
    """
    from shapely.geometry import box

    clipped = gdf.geometry.intersection(box(*bbox))
    out = gdf.assign(geometry=clipped).explode(index_parts=False)
    out = out[out.geom_type == "LineString"]
    return out[~out.is_empty].reset_index(drop=True)


def _overture_roads_path(cache_dir: Path, bbox, release: str, highway) -> Path:
    key = _key(_round_bbox(bbox), sorted(highway), "overture-roads-v1")
    return cache_dir / f"overture_roads_{release}_{key}.gpkg"


def _overture_buildings_path(cache_dir: Path, bbox, release: str) -> Path:
    key = _key(_round_bbox(bbox), "overture-buildings-v1")
    return cache_dir / f"overture_buildings_{release}_{key}.gpkg"


def _resolve_overture_release(release, bbox, cache_dir, offline) -> str:
    """
    Return the release to use.

    An explicit ``release`` wins. Online, the latest release is used. Offline,
    the newest release that has cached buildings for this box is used.
    """
    if release:
        if not _OVERTURE_RELEASE_RE.match(str(release)):
            raise ValueError(
                f"overture_release must look like '2026-11-18.0', got {release!r}."
            )
        return str(release)
    if not offline:
        return _overture_latest_release()
    key = _key(_round_bbox(bbox), "overture-buildings-v1")
    prefix, suffix = "overture_buildings_", f"_{key}.gpkg"

    def _release_key(path: Path) -> tuple:
        # "2026-11-18.10" must sort after "2026-11-18.9"
        release = path.name[len(prefix) : -len(suffix)]
        return tuple(int(part) for part in re.split(r"[-.]", release) if part.isdigit())

    found = sorted(cache_dir.glob(f"{prefix}*{suffix}"), key=_release_key)
    if not found:
        raise OfflineCacheMissError(
            "offline=True but no cached Overture buildings for this area "
            f"in {cache_dir}."
        )
    return found[-1].name[len(prefix) : -len(suffix)]


def _fetch_overture_roads(bbox, highway, cache_dir, offline, release):
    path = _overture_roads_path(cache_dir, bbox, release, highway)
    cached = _cached_gpkg(path, offline, "Overture roads")
    if cached is not None:
        return cached, True
    raw = _read_overture("segment", bbox, release)
    if "subtype" in raw:
        raw = raw[raw["subtype"] == "road"]
    raw = _within_box(raw, bbox)
    n = len(raw)
    mapped = [
        _overture_highway(c, sc)
        for c, sc in zip(
            raw["class"] if "class" in raw else [None] * n,
            raw["subclass"] if "subclass" in raw else [None] * n,
            strict=True,
        )
    ]
    keep = np.array([m is not None and m in highway for m in mapped], dtype=bool)
    names = raw["names"] if "names" in raw else [None] * n
    out = gpd.GeoDataFrame(
        {
            "id": raw["id"].astype(str).to_numpy() if "id" in raw else [""] * n,
            "highway": mapped,
            "name": [_overture_name(v) for v in names],
        },
        geometry=raw.geometry.values,
        crs=4326,
    )[keep]
    out = _clip_lines(out, bbox)
    out = out.reset_index(drop=True)
    if out.empty:
        raise FetchError("Overture has no roads in this area")
    _write_gpkg_atomic(out, path)
    return out, False


def _fetch_overture_buildings(bbox, cache_dir, offline, release):
    path = _overture_buildings_path(cache_dir, bbox, release)
    cached = _cached_gpkg(path, offline, "Overture buildings")
    if cached is not None:
        return cached, True
    raw = _read_overture("building", bbox, release)
    raw = _within_box(raw, bbox)
    raw = raw[raw.geom_type.isin(["Polygon", "MultiPolygon"])]
    if "is_underground" in raw:
        raw = raw[~(raw["is_underground"].fillna(False).astype(bool))]
    n = len(raw)
    height = raw["height"] if "height" in raw else [None] * n
    classes = raw["class"] if "class" in raw else [None] * n
    out = gpd.GeoDataFrame(
        {
            "id": raw["id"].astype(str).to_numpy() if "id" in raw else [""] * n,
            "building": [_join(v) for v in classes],
            "height": [None if _join(v) == "" else float(v) for v in height],
        },
        geometry=raw.geometry.values,
        crs=4326,
    ).reset_index(drop=True)
    if out.empty:
        raise FetchError("Overture has no buildings in this area")
    _write_gpkg_atomic(out, path)
    return out, False


def _filter_small_buildings(
    buildings: gpd.GeoDataFrame, min_area_m2: float = OVERTURE_MIN_BUILDING_AREA_M2
) -> gpd.GeoDataFrame:
    """Drop footprints smaller than ``min_area_m2`` (``buildings`` is projected)."""
    out = buildings[buildings.geometry.area >= min_area_m2].reset_index(drop=True)
    if out.empty:
        raise FetchError(
            f"Overture has no buildings of {min_area_m2:g} m2 or more in this area"
        )
    return out


# --------------------------------------------------------------------------
# DEM
# --------------------------------------------------------------------------


def _tile_origins(bbox, dlon: float, dlat: float) -> list[tuple[int, int]]:
    """SW corners ``(lat, lon)`` of the 1 degree tiles that touch the box."""
    w, s, e, n = bbox
    lon0 = max(math.floor(w - dlon), -180)
    lon1 = min(math.floor(e + dlon), 179)
    lat0 = max(math.floor(s - dlat), -90)
    lat1 = min(math.floor(n + dlat), 89)
    return [
        (lat, lon) for lat in range(lat0, lat1 + 1) for lon in range(lon0, lon1 + 1)
    ]


def _tile_url(lat: int, lon: int) -> str:
    return COPERNICUS_URL_TEMPLATE.format(
        ns="N" if lat >= 0 else "S",
        lat=abs(lat),
        ew="E" if lon >= 0 else "W",
        lon=abs(lon),
    )


def _write_tif(path: Path, data, crs, transform, nodata) -> None:
    import rasterio

    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=nodata,
        compress="deflate",
    ) as ds:
        ds.write(data, 1)


def _fetch_copernicus(bbox, crs, out_path: Path) -> None:
    """Write the GLO-30 DEM of ``bbox`` (+ margin) in ``crs`` to ``out_path``."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.errors import RasterioIOError
    from rasterio.merge import merge
    from rasterio.transform import from_origin
    from rasterio.warp import reproject, transform_bounds

    w, s, e, n = bbox
    dlon, dlat = _margins(bbox)
    ext = (w - dlon, s - dlat, e + dlon, n + dlat)
    # Read a window with twice the margin so that the rotated output grid
    # has no nodata at its edges. The tiles use the same window.
    read_ext = (w - 2 * dlon, s - 2 * dlat, e + 2 * dlon, n + 2 * dlat)

    datasets = []
    env = rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        AWS_NO_SIGN_REQUEST="YES",
        GDAL_HTTP_MAX_RETRY="3",
        GDAL_HTTP_RETRY_DELAY="1",
    )
    with env:
        try:
            for lat, lon in _tile_origins(bbox, 2 * dlon, 2 * dlat):
                url = _tile_url(lat, lon)
                try:
                    datasets.append(rasterio.open(url))
                except RasterioIOError as exc:
                    # GLO-30 has no tiles over open ocean; those are 404s.
                    msg = str(exc).lower()
                    if "404" in msg or "not recognized" in msg or "not found" in msg:
                        logger.info(
                            "No Copernicus tile at %s, skipped (raw error: %s)",
                            url,
                            exc,
                        )
                        continue
                    if any(m in msg for m in _NETWORK_MARKERS):
                        raise ConnectionError(
                            f"Could not reach the Copernicus DEM server: {exc}"
                        ) from exc
                    raise
            if not datasets:
                raise FetchError(
                    "No Copernicus GLO-30 tile covers this area (open ocean?). "
                    "Pass a local DEM with dem_source=<path>."
                )
            arr, transform = merge(
                datasets, bounds=read_ext, nodata=_NODATA, dtype="float32"
            )
            src_crs = datasets[0].crs
        finally:
            for ds in datasets:
                ds.close()

        tb = transform_bounds("EPSG:4326", crs, *ext, densify_pts=21)
        res = _DEM_RES_M
        x0 = math.floor(tb[0] / res) * res
        y1 = math.ceil(tb[3] / res) * res
        width = max(math.ceil((tb[2] - x0) / res), 1)
        height = max(math.ceil((y1 - tb[1]) / res), 1)
        dst_transform = from_origin(x0, y1, res, res)
        dst = np.full((height, width), _NODATA, dtype="float32")
        reproject(
            source=arr[0],
            destination=dst,
            src_transform=transform,
            src_crs=src_crs,
            src_nodata=_NODATA,
            dst_transform=dst_transform,
            dst_crs=crs,
            dst_nodata=_NODATA,
            resampling=Resampling.bilinear,
        )
        _write_tif(out_path, dst, crs, dst_transform, _NODATA)


#: DEM downloaders by name: ``fetcher(bbox_wgs84, target_crs, out_path)``.
#: NASADEM and FABDEM are planned for M2. FABDEM is CC BY-NC-SA and will
#: need an explicit opt-in that is recorded in the provenance.
DEM_SOURCES: dict[str, Callable] = {"copernicus": _fetch_copernicus}

_DEM_PROVENANCE = {
    "copernicus": {
        "source": "Copernicus GLO-30",
        "url": COPERNICUS_URL_TEMPLATE,
        "attribution": COPERNICUS_DERIVED_ATTRIBUTION,
    }
}


def _prepare_local_dem(bbox, path: Path, crs, cache_dir: Path):
    """
    Return ``(dem_path, crs, cached)`` for a local DEM.

    Only the box plus margin is read. A DEM that is already in the target CRS
    and at most 4 times the size of that window is passed through; otherwise
    the window is cropped (and reprojected) into the cache.
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import calculate_default_transform, reproject, transform_bounds
    from rasterio.windows import Window, from_bounds

    if not path.exists():
        raise FetchError(f"The DEM file does not exist: {path}")
    with rasterio.open(path) as src:
        if src.crs is None:
            raise FetchError(f"The DEM has no CRS: {path}")
        target = crs
        if target is None:
            target = src.crs if src.crs.is_projected else _utm_crs(bbox)
        w, s, e, n = bbox
        dlon, dlat = _margins(bbox, 2.0)
        b = transform_bounds(
            "EPSG:4326", src.crs, w - dlon, s - dlat, e + dlon, n + dlat, densify_pts=21
        )
        win = from_bounds(*b, transform=src.transform)
        win = win.round_offsets(op="floor").round_lengths(op="ceil")
        full = Window(0, 0, src.width, src.height)
        try:
            win = win.intersection(full)
        except Exception as exc:
            raise FetchError(
                f"The DEM {path} does not overlap the bounding box."
            ) from exc
        if win.width < 1 or win.height < 1:
            raise FetchError(f"The DEM {path} does not overlap the bounding box.")
        if src.crs == target and (
            src.width * src.height <= _PASS_THROUGH_FACTOR * win.width * win.height
        ):
            return path, target, False
        key = _key(
            str(path.resolve()),
            path.stat().st_mtime_ns,
            _round_bbox(bbox),
            target.to_string(),
        )
        out = cache_dir / f"dem_file_{key}.tif"
        if not out.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            nodata = src.nodata if src.nodata is not None else _NODATA
            data = src.read(1, window=win, masked=False).astype("float32")
            wtransform = src.window_transform(win)
            if src.crs == target:
                dst, transform = data, wtransform
            else:
                wb = rasterio.windows.bounds(win, src.transform)
                transform, width, height = calculate_default_transform(
                    src.crs, target, data.shape[1], data.shape[0], *wb
                )
                dst = np.full((height, width), nodata, dtype="float32")
                reproject(
                    source=data,
                    destination=dst,
                    src_transform=wtransform,
                    src_crs=src.crs,
                    src_nodata=src.nodata,
                    dst_transform=transform,
                    dst_crs=target,
                    dst_nodata=nodata,
                    resampling=Resampling.bilinear,
                )
            tmp = out.with_suffix(f".part{os.getpid()}")
            try:
                _write_tif(tmp, dst, target, transform, nodata)
                os.replace(tmp, out)
            finally:
                tmp.unlink(missing_ok=True)
    return out, target, True


def _utm_crs(bbox):
    from pyproj import CRS
    from shapely.geometry import box

    gdf = gpd.GeoDataFrame(geometry=[box(*bbox)], crs=4326)
    return CRS.from_user_input(gdf.estimate_utm_crs())


def _resolve_dem_source(dem_source) -> tuple[str, Callable | Path]:
    if isinstance(dem_source, str) and dem_source in DEM_SOURCES:
        return "registry", DEM_SOURCES[dem_source]
    if isinstance(dem_source, os.PathLike) or (
        isinstance(dem_source, str) and Path(dem_source).is_file()
    ):
        return "file", Path(dem_source)
    raise ValueError(
        f"unknown dem_source {str(dem_source)!r}; known: "
        f"{', '.join(DEM_SOURCES)} or a file path"
    )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _dem_layer(bbox, kind, dem_obj, dem_source, target, cache, offline):
    """
    Get the DEM of ``bbox``: ``(path, crs, cache_hit, layer, top_level, cached)``.

    ``layer`` is the provenance of the DEM, ``top_level`` the ``dem_*`` keys of
    the provenance of :func:`fetch_inputs`, ``cached`` the files whose age
    gives ``fetched_at``.
    """
    cached_files = []
    if kind == "file":
        dem_path, target, dem_cached = _prepare_local_dem(bbox, dem_obj, target, cache)
        dem_hit = dem_cached
        dem_prov = {"source": "file", "path": str(dem_source)}
        prov_dem = {"dem_source": "file", "dem_path": str(dem_source)}
        if dem_cached:
            cached_files.append(dem_path)
    else:
        name = dem_source
        if target is None:
            target = _utm_crs(bbox)
        key = _key(_round_bbox(bbox), target.to_string(), name)
        dem_path = cache / f"dem_{name}_{key}.tif"
        dem_hit = dem_path.exists()
        if not dem_hit:
            if offline:
                raise OfflineCacheMissError(
                    f"offline=True but no cached DEM for this area (expected {dem_path})."
                )
            tmp = dem_path.with_suffix(f".part{os.getpid()}")
            try:
                dem_obj(bbox, target, tmp)
                os.replace(tmp, dem_path)
            finally:
                tmp.unlink(missing_ok=True)
        dem_prov = dict(_DEM_PROVENANCE[name])
        prov_dem = {"dem_source": name, "dem_url_template": dem_prov["url"]}
        cached_files.append(dem_path)
    return dem_path, target, dem_hit, dem_prov, prov_dem, cached_files


def fetch_dem(
    bbox_wgs84: tuple[float, float, float, float],
    *,
    cache_dir: str | os.PathLike | None = None,
    dem_source: str | os.PathLike = "copernicus",
    crs: str | int | None = None,
    offline: bool = False,
    retries: int = 3,
    retry_wait: float = 5.0,
    progress: Progress | None = None,
) -> tuple[Path, dict]:
    """
    Fetch only the DEM of a bounding box (the DEM part of :func:`fetch_inputs`).

    It uses the same cache files and keys as :func:`fetch_inputs`, so a later
    :func:`fetch_inputs` call for the same box reuses the file. There is no
    area limit here.

    Parameters
    ----------
    bbox_wgs84 : tuple of float
        ``(west, south, east, north)`` in decimal degrees (EPSG:4326).
    cache_dir, dem_source, crs, offline
        As in :func:`fetch_inputs`.
    retries, retry_wait : int, float
        Accepted for symmetry with :func:`fetch_inputs`; the DEM download
        retries through GDAL and does not use them.
    progress : callable, optional
        ``progress(stage, fraction, message)`` with the stage ``fetch:dem``.

    Returns
    -------
    tuple of (pathlib.Path, dict)
        The projected GeoTIFF and its provenance: ``source``, ``url``,
        ``attribution`` (the derived Copernicus string), ``cache_hit``,
        ``crs``, ``bbox_wgs84`` and ``fetched_at`` (UTC ISO time of the cached
        file; ``None`` when a local DEM is passed through uncached).

    Raises
    ------
    OfflineCacheMissError
        ``offline=True`` and the DEM is not cached.
    ValueError
        Invalid box, CRS or ``dem_source``.
    """
    from pyproj import CRS

    bbox = _validate_bbox(bbox_wgs84, float("inf"))
    cache = Path(cache_dir) if cache_dir is not None else _default_cache_dir()
    kind, dem_obj = _resolve_dem_source(dem_source)
    target = CRS.from_user_input(crs) if crs is not None else None
    if target is not None and not target.is_projected:
        raise ValueError(f"crs must be projected (metres), got {target.to_string()}.")
    if not offline:
        cache.mkdir(parents=True, exist_ok=True)
    _report(progress, "fetch:dem", 0.0, "Preparing the DEM")
    path, target, hit, layer, _, cached = _dem_layer(
        bbox, kind, dem_obj, dem_source, target, cache, offline
    )
    fetched_at = None
    if cached:
        fetched_at = datetime.fromtimestamp(
            min(p.stat().st_mtime for p in cached), timezone.utc
        ).isoformat(timespec="seconds")
    epsg = target.to_epsg()
    prov = {
        **layer,
        "cache_hit": hit,
        "crs": f"EPSG:{epsg}" if epsg else target.to_string(),
        "bbox_wgs84": list(bbox),
        "fetched_at": fetched_at,
    }
    _report(progress, "fetch:dem", 1.0, "DEM ready")
    return path, prov


def fetch_inputs(
    bbox_wgs84: tuple[float, float, float, float],
    *,
    cache_dir: str | os.PathLike | None = None,
    dem_source: str | os.PathLike = "copernicus",
    crs: str | int | None = None,
    max_area_km2: float = 4.0,
    highway: tuple[str, ...] | None = None,
    progress: Progress | None = None,
    offline: bool = False,
    retries: int = 3,
    retry_wait: float = 5.0,
    overpass_url: str | None = None,
    source: str = "osm",
    overture_release: str | None = None,
) -> Inputs:
    """
    Fetch roads, buildings and a DEM for a bounding box.

    GLO-30 is a digital surface model (DSM): buildings and trees raise the
    ground in towns. Its resolution is 30 m. For a better result pass a local
    bare-earth DEM with ``dem_source``.

    This function is not thread-safe: it swaps global osmnx settings while it
    downloads, and its temporary files are named by process id only.

    With ``offline=True`` and a local DEM, the cache folder is neither created
    nor written when the DEM can be passed through; it is written only if the
    DEM must be cropped or reprojected.

    Parameters
    ----------
    bbox_wgs84 : tuple of float
        ``(west, south, east, north)`` in decimal degrees (EPSG:4326).
    cache_dir : str or os.PathLike, optional
        Cache folder. Default: ``~/.cache/pysewer/sources`` (or
        ``%LOCALAPPDATA%\\pysewer\\sources`` on Windows). osmnx's HTTP cache
        goes to its ``osmnx`` sub-folder.
    dem_source : str or os.PathLike, default "copernicus"
        A key of :data:`DEM_SOURCES` (``"copernicus"``: GLO-30 COGs from AWS
        open data) or the path of a local DEM file (an ``os.PathLike`` or an
        existing file). Only the box plus margin of a local DEM is used; it
        is cropped and reprojected into the cache unless it is already in
        the target CRS and small. Without ``crs`` a projected local DEM keeps
        its own CRS. Any other value raises ``ValueError``.
    crs : str or int, optional
        Projected target CRS. Default: the UTM zone of the box.
    max_area_km2 : float, default 4.0
        Area limit of the box, computed on the ellipsoid.
    highway : tuple of str, optional
        OSM ``highway`` classes to keep. Default: :data:`DEFAULT_HIGHWAY`
        (primary, secondary, tertiary, unclassified, residential,
        living_street, road, the ``*_link`` classes and service roads except
        ``service=parking_aisle`` and ``service=driveway``).
    progress : callable, optional
        ``progress(stage, fraction, message)``. Stages: ``fetch:roads``,
        ``fetch:buildings``, ``fetch:dem``, ``fetch:done``.
    offline : bool, default False
        Use only cached data; raise :class:`OfflineCacheMissError` if it is
        missing.
    retries : int, default 3
        Retries on Overpass server errors and connection errors.
    retry_wait : float, default 5.0
        First wait between retries in seconds; it doubles each time, and the
        total wait never exceeds ``retries * retry_wait``.
    overpass_url : str, optional
        Overpass server for ``source="osm"``. Order: this argument, the
        environment variable ``PYSEWER_OVERPASS_URL``, the osmnx default
        (``https://overpass-api.de/api``). ``.../api`` and
        ``.../api/interpreter`` are both accepted. The URL is set only while
        the download runs and does not change the cache key; it is recorded in
        ``provenance["overpass_url"]``.
    source : {"osm", "overture"}, default "osm"
        Source of roads and buildings. ``"osm"`` queries Overpass.
        ``"overture"`` reads the Overture Maps GeoParquet on AWS S3 (needs the
        ``overturemaps`` package). Overture roads keep ``subtype == "road"``
        and are mapped to OSM ``highway`` values (motorways, trunks and
        footpaths are dropped). Overture buildings drop underground buildings
        and footprints under 20 m2 (computed in the target CRS). Both layers
        are ODbL; see :data:`OVERTURE_ATTRIBUTION`.
    overture_release : str, optional
        Overture release such as ``"2026-11-18.0"``. Default: the latest
        release online, and the newest cached release for the box offline.
        Cache files are keyed by bbox, source and release.

    Returns
    -------
    pysewer.pipeline.Inputs
        DEM path, roads and buildings (GeoDataFrames in the working CRS) and
        provenance with attribution strings. ``provenance["fetched_at"]`` is
        the time of the oldest cached layer used; ``cache_hit`` is set per
        layer.

    Raises
    ------
    AreaTooLargeError
        The box is larger than ``max_area_km2``.
    FetchError
        Empty OSM result or unusable DEM (a ``ValueError``).
    OfflineCacheMissError
        ``offline=True`` and the data is not cached (also a
        ``FileNotFoundError``).
    ValueError
        Invalid box, CRS or ``dem_source``.
    """
    from pyproj import CRS

    bbox = _validate_bbox(bbox_wgs84, max_area_km2)
    highway = tuple(highway) if highway else DEFAULT_HIGHWAY
    cache = Path(cache_dir) if cache_dir is not None else _default_cache_dir()
    kind, dem_obj = _resolve_dem_source(dem_source)

    target = CRS.from_user_input(crs) if crs is not None else None
    if target is not None and not target.is_projected:
        raise ValueError(f"crs must be projected (metres), got {target.to_string()}.")
    if not offline:
        cache.mkdir(parents=True, exist_ok=True)

    _check_source(source)
    overpass = resolve_overpass_url(overpass_url) if source == "osm" else None
    label = "OSM" if source == "osm" else "Overture"
    release = None
    if source == "overture":
        release = _resolve_overture_release(overture_release, bbox, cache, offline)

    _report(progress, "fetch:roads", 0.0, f"Fetching {label} roads")
    if source == "osm":
        roads_wgs, roads_hit = _fetch_roads(
            bbox, highway, cache, offline, retries, retry_wait, overpass
        )
    else:
        roads_wgs, roads_hit = _fetch_overture_roads(
            bbox, highway, cache, offline, release
        )
    _report(progress, "fetch:roads", 0.3, f"{len(roads_wgs)} road segments")
    _report(progress, "fetch:buildings", 0.3, f"Fetching {label} buildings")
    if source == "osm":
        buildings_wgs, bld_hit = _fetch_buildings(
            bbox, cache, offline, retries, retry_wait, overpass
        )
    else:
        buildings_wgs, bld_hit = _fetch_overture_buildings(
            bbox, cache, offline, release
        )
    _report(progress, "fetch:buildings", 0.6, f"{len(buildings_wgs)} buildings")

    _report(progress, "fetch:dem", 0.6, "Preparing the DEM")
    dem_path, target, dem_hit, dem_prov, prov_dem, cached_files = _dem_layer(
        bbox, kind, dem_obj, dem_source, target, cache, offline
    )

    roads = roads_wgs.to_crs(target)
    buildings = buildings_wgs.to_crs(target)
    epsg = target.to_epsg()
    if source == "osm":
        layer = {
            "source": "OpenStreetMap",
            "url": "https://www.openstreetmap.org",
            "attribution": OSM_ATTRIBUTION,
        }
        bld_layer = dict(layer)
        cached_files += [
            _roads_cache_path(cache, bbox, highway),
            _buildings_cache_path(cache, bbox),
        ]
        extra = {
            "source": "osm",
            "osm": {"highway": list(highway), "source": OSM_ATTRIBUTION},
            "overpass_url": _effective_overpass_url(overpass),
        }
    else:
        n_raw = len(buildings)
        buildings = _filter_small_buildings(buildings)
        bld_filter = {
            "underground": "dropped (is_underground == true)",
            "min_area_m2": OVERTURE_MIN_BUILDING_AREA_M2,
            "geometry": ["Polygon", "MultiPolygon"],
            "dropped_small": n_raw - len(buildings),
        }
        _report(
            progress,
            "fetch:buildings",
            0.6,
            f"{len(buildings)} buildings after the area filter",
        )
        layer = {
            "source": "Overture Maps Foundation",
            "url": "https://overturemaps.org",
            "release": release,
            "license": "ODbL",
            "attribution": OVERTURE_ATTRIBUTION,
        }
        bld_layer = {**layer, "buildings_filter": bld_filter}
        cached_files += [
            _overture_roads_path(cache, bbox, release, highway),
            _overture_buildings_path(cache, bbox, release),
        ]
        extra = {
            "source": "overture",
            "overture": {
                "release": release,
                "highway": list(highway),
                "source": OVERTURE_ATTRIBUTION,
            },
            "buildings_filter": bld_filter,
            "overpass_url": None,
        }
    oldest = min(p.stat().st_mtime for p in cached_files if p.exists())
    fetched_at = datetime.fromtimestamp(oldest, timezone.utc)
    provenance = {
        "bbox_wgs84": list(bbox),
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        **prov_dem,
        **extra,
        "crs": f"EPSG:{epsg}" if epsg else target.to_string(),
        "dem": {**dem_prov, "cache_hit": dem_hit},
        "roads": {**layer, "cache_hit": roads_hit},
        "buildings": {**bld_layer, "cache_hit": bld_hit},
    }
    _report(progress, "fetch:done", 1.0, "Inputs ready")
    return Inputs(dem=dem_path, roads=roads, buildings=buildings, provenance=provenance)
