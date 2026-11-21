# SPDX-License-Identifier: GPL-3.0-only
"""
Command line interface of pysewer.

Subcommands: ``run`` (design a network), ``fetch`` (download inputs into a
cache), ``report`` (HTML report of bundles), ``compare`` (compare bundles) and
``profile`` (longitudinal section of one flow path).

Only the standard library is imported at module level. Each handler imports
the heavy modules (geopandas, matplotlib, osmnx, ...) when it runs, so
``pysewer --help`` stays fast.

Exit codes
----------
0
    Success.
1
    Unexpected error (traceback only with ``--debug``).
2
    Usage or argument error (argparse).
3
    Input or validation error (``ValueError``, ``FileNotFoundError``,
    including ``FetchError``).
4
    The bounding box is larger than the area limit (``AreaTooLargeError``).
5
    ``--offline`` and the cache has no data for the box
    (``OfflineCacheMissError``).
6
    The network is not reachable (Overpass or the DEM server).
130
    Interrupted with Ctrl-C.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

__all__ = ["build_parser", "main", "parse_bbox", "parse_overrides", "parse_sink"]

PROGRESS_SCHEMA_VERSION = 1

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_AREA = 4
EXIT_OFFLINE = 5
EXIT_NETWORK = 6
EXIT_INTERRUPT = 130

_NO_SOURCES = "pysewer.sources is not available in this version"
_NO_PLOT = "this needs matplotlib; install it with: pip install 'pysewer[plot]'"


class CliError(Exception):
    """An error with a plain message and an exit code."""

    def __init__(self, message: str, code: int = EXIT_INPUT):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# Argument parsing helpers (pure functions)
# --------------------------------------------------------------------------


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    """
    Parse ``"W,S,E,N"`` into a tuple of four floats (WGS84 degrees).

    Raises
    ------
    argparse.ArgumentTypeError
        If the text does not have four numbers.
    """
    try:
        values = tuple(float(v) for v in text.split(","))
    except ValueError:
        values = ()
    if len(values) != 4:
        raise argparse.ArgumentTypeError(
            f"expected four comma-separated numbers W,S,E,N, got {text!r}"
        )
    return values  # type: ignore[return-value]


def parse_sink(text: str) -> str | tuple[float, float]:
    """
    Parse ``"lowest"`` or ``"X,Y"``.

    Raises
    ------
    argparse.ArgumentTypeError
        For any other text.
    """
    if text.strip().lower() == "lowest":
        return "lowest"
    try:
        values = tuple(float(v) for v in text.split(","))
    except ValueError:
        values = ()
    if len(values) != 2:
        raise argparse.ArgumentTypeError(
            f"expected 'lowest' or two comma-separated numbers X,Y, got {text!r}"
        )
    return values  # type: ignore[return-value]


def _parse_value(text: str) -> Any:
    """Parse a value as JSON if possible, else keep the string."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def _nest(target: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = target
    for part in parts[:-1]:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"conflicting override keys at {part!r}")
        node = child
    node[parts[-1]] = value


# Convenience flag -> config key.
_CONVENIENCE = {
    "tmax": "optimization.tmax",
    "tmin": "optimization.tmin",
    "people_per_building": "optimization.inhabitants_dwelling",
    "station_cost": "optimization.station_cost",
}


def parse_overrides(
    sets: Sequence[str] | None = None, **convenience: Any
) -> dict[str, dict]:
    """
    Build the nested config overrides from CLI values.

    Parameters
    ----------
    sets : sequence of str, optional
        Items ``"section.key=value"``. The value is parsed as JSON if possible
        (``3``, ``true``, ``[1, 2]``), else it stays a string. They win over
        the convenience values.
    **convenience
        ``tmax``, ``tmin``, ``people_per_building``, ``station_cost``
        (None is ignored).

    Returns
    -------
    dict
        For example ``{"optimization": {"tmax": 3.0}}``.

    Raises
    ------
    ValueError
        If an item has no ``=`` or no ``section.key`` form.
    """
    out: dict = {}
    for flag, dotted in _CONVENIENCE.items():
        value = convenience.get(flag)
        if value is not None:
            _nest(out, dotted, value)
    for item in sets or ():
        key, sep, raw = item.partition("=")
        key = key.strip()
        if not sep or "." not in key or not all(key.split(".")):
            raise ValueError(f"--set expects section.key=value, got {item!r}")
        _nest(out, key, _parse_value(raw))
    return out


def _make_progress(mode: str) -> Callable[[str, float, str], None] | None:
    """Return a progress callback that writes to stderr, or None."""
    if mode == "none":
        return None

    def emit(stage: str, fraction: float, message: str) -> None:
        if mode == "jsonl":
            line = json.dumps(
                {
                    "v": PROGRESS_SCHEMA_VERSION,
                    "phase": "fetch" if stage.startswith("fetch:") else "generate",
                    "stage": stage,
                    "fraction": round(float(fraction), 4),
                    "message": message,
                }
            )
        else:
            line = f"[{stage}] {fraction * 100:3.0f}%  {message}"
        print(line, file=sys.stderr, flush=True)

    return emit


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------


def _dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, allow_nan=False, default=str)


def _fmt(value: Any, unit: str = "", digits: int = 1) -> str:
    if value is None:
        return "n/a"
    try:
        if value != value:  # NaN
            return "n/a"
        text = f"{value:,.{digits}f}" if isinstance(value, float) else f"{value:,}"
    except (TypeError, ValueError):
        return str(value)
    return f"{text} {unit}".rstrip()


def _summary_lines(name: str, metrics: Mapping) -> list[str]:
    g = metrics.get
    return [
        f"network {name}",
        f"  pipes: {_fmt(g('n_pipes'))} "
        f"(total {_fmt(g('total_length_m'), 'm', 0)}, "
        f"gravity {_fmt(g('gravity_length_m'), 'm', 0)}, "
        f"pressurized {_fmt(g('pressurized_length_m'), 'm', 0)})",
        f"  buildings connected: {_fmt(g('buildings_connected'))} "
        f"of {_fmt(g('buildings_total'))}",
        f"  pumping stations: {_fmt(g('n_pumping_stations'))}, "
        f"lifting stations: {_fmt(g('n_lifting_stations'))}, "
        f"private pumps: {_fmt(g('n_private_pumps'))}",
        f"  max depth: {_fmt(g('max_depth_m'), 'm')}, "
        f"longest flow path: {_fmt(g('longest_flow_path_m'), 'm', 0)}",
    ]


def _resolve_out_format(path: Path, fmt: str | None) -> str:
    if fmt:
        return fmt
    return "gpkg" if path.suffix.lower() == ".gpkg" else "parquet"


def _unique_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n}_{seen[n]}")
    return out


def _read_bundles(paths: Sequence[str], names: str | None) -> dict:
    from .bundle import NetworkBundle

    bundles = []
    default_names = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            raise CliError(f"bundle not found: {path}")
        bundle = NetworkBundle.read(path)
        bundles.append(bundle)
        meta_name = (bundle.metadata or {}).get("name")
        default_names.append(str(meta_name) if meta_name else path.stem)
    if names:
        given = [n.strip() for n in names.split(",")]
        if len(given) != len(bundles) or not all(given):
            raise CliError(
                f"--names needs {len(bundles)} comma-separated names, got {names!r}"
            )
        labels = given
    else:
        labels = default_names
    return dict(zip(_unique_names(labels), bundles, strict=True))


# --------------------------------------------------------------------------
# Fetch support
# --------------------------------------------------------------------------


def _check_bbox(bbox: Sequence[float], max_area_km2: float | None) -> None:
    """Validate the box and its ellipsoidal area before any network access."""
    import math

    limit = 4.0 if max_area_km2 is None else max_area_km2
    w, s, e, n = bbox
    if not all(math.isfinite(v) for v in bbox):
        raise CliError("bbox contains non-finite values.")
    if not (-180 <= w < e <= 180):
        raise CliError(
            f"bbox needs -180 <= west < east <= 180, got west={w}, east={e}."
        )
    if not (-90 <= s < n <= 90):
        raise CliError(
            f"bbox needs -90 <= south < north <= 90, got south={s}, north={n}."
        )
    from pyproj import Geod
    from shapely.geometry import box

    area, _ = Geod(ellps="WGS84").geometry_area_perimeter(box(w, s, e, n))
    km2 = abs(area) / 1e6
    if km2 > limit:
        raise CliError(
            f"The bounding box covers {km2:.2f} km2, more than the limit of "
            f"{limit:g} km2. Use a smaller box or raise --max-area.",
            EXIT_AREA,
        )


def _unreachable(host: str | None) -> str:
    where = f" {host}" if host else ""
    return (
        f"cannot reach{where} (OpenStreetMap Overpass or Copernicus DEM). "
        "Check the internet connection or use --offline with a filled cache."
    )


def _probe(url: str, method: str, timeout: float = 5.0) -> None:
    """Request ``url``; any HTTP status counts as reachable."""
    import urllib.error
    import urllib.request
    from urllib.parse import urlparse

    req = urllib.request.Request(url, method=method)
    try:
        urllib.request.urlopen(req, timeout=timeout).close()
    except urllib.error.HTTPError:
        return  # the server answered
    except (OSError, ValueError) as exc:  # URLError, timeout, refused, DNS
        raise CliError(_unreachable(urlparse(url).hostname), EXIT_NETWORK) from exc


def _preflight(dem_source: str) -> None:
    """Fail fast (exit 6) when Overpass or the Copernicus bucket is unreachable."""
    try:
        import osmnx

        base = str(osmnx.settings.overpass_url).rstrip("/")
    except Exception:  # osmnx missing: let fetch_inputs report it
        return
    if base.endswith("/interpreter"):
        base = base.rsplit("/", 1)[0]
    _probe(base + "/status", "GET")
    if str(dem_source) == "copernicus":
        _probe("https://copernicus-dem-30m.s3.amazonaws.com/", "HEAD")


def _import_sources():
    try:
        from . import sources
    except (ImportError, AttributeError) as exc:
        raise CliError(f"{_NO_SOURCES} ({exc})", EXIT_ERROR) from exc
    return sources


def _fetch(args: argparse.Namespace, progress):
    sources = _import_sources()
    _check_bbox(args.bbox, args.max_area)
    if not args.offline:
        _preflight(args.dem_source)
    kwargs: dict[str, Any] = {
        "cache_dir": args.cache_dir,
        "dem_source": args.dem_source,
        "crs": args.crs,
        "offline": args.offline,
        "progress": progress,
        "retries": args.retries,
        "retry_wait": args.retry_wait,
    }
    if args.max_area is not None:
        kwargs["max_area_km2"] = args.max_area
    try:
        fetch_inputs = sources.fetch_inputs
    except AttributeError as exc:
        raise CliError(_NO_SOURCES, EXIT_ERROR) from exc
    return fetch_inputs(args.bbox, **kwargs)


def _exit_code_for(exc: BaseException) -> int | None:
    """Map a fetch error to its exit code by class name (no import needed)."""
    names = {c.__name__ for c in type(exc).__mro__}
    if "OfflineCacheMissError" in names:
        return EXIT_OFFLINE
    if "AreaTooLargeError" in names:
        return EXIT_AREA
    return None


_NETWORK_NAMES = {
    "ConnectionError",
    "Timeout",
    "ConnectTimeout",
    "ReadTimeout",
    "HTTPError",
    "RequestException",
    "ResponseStatusCodeError",
    "HTTPException",
    "NewConnectionError",
    "MaxRetryError",
    "ProtocolError",
    "TimeoutError",
}


def _network_failure(exc: BaseException) -> tuple[bool, str | None]:
    """Tell if ``exc`` or its cause chain is a network failure; give the host."""
    import re

    seen: set[int] = set()
    found = False
    host = None
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        mro = {c.__name__ for c in type(cur).__mro__}
        modules = {c.__module__.split(".")[0] for c in type(cur).__mro__}
        if mro & _NETWORK_NAMES and modules & {
            "requests",
            "urllib3",
            "osmnx",
            "socket",
        }:
            found = True
        if found and host is None:
            m = re.search(r"host='([^']+)'", str(cur))
            host = m.group(1) if m else None
        cur = cur.__cause__ or cur.__context__
    return found, host


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    from .metrics import network_metrics
    from .pipeline import Inputs, generate

    progress = _make_progress(args.progress)
    overrides = parse_overrides(
        args.set,
        tmax=args.tmax,
        tmin=args.tmin,
        people_per_building=args.people_per_building,
        station_cost=args.station_cost,
    )
    if args.bbox is not None:
        inputs = _fetch(args, progress)
    else:
        inputs = Inputs(dem=args.dem, roads=args.roads, buildings=args.buildings)

    out = Path(args.out) if args.out else None
    name = args.name or (out.stem if out else "network")
    bundle = generate(
        inputs,
        sink=args.sink,
        sink_crs=args.sink_crs,
        overrides=overrides,
        name=name,
        progress=progress,
    )
    metrics = network_metrics(bundle)

    written = []
    if out is not None:
        fmt = _resolve_out_format(out, args.format)
        written.append(bundle.write(out, fmt))
    if args.report:
        from .viz.report import build_report

        written.append(build_report(bundle, args.report, title=name))

    if args.json:
        print(_dumps(metrics))
    else:
        print("\n".join(_summary_lines(name, metrics)))
        for path in written:
            print(f"  wrote {path}")
    return EXIT_OK


def _cmd_fetch(args: argparse.Namespace) -> int:
    inputs = _fetch(args, _make_progress(args.progress))
    prov = inputs.provenance or {}
    dem = Path(inputs.dem)
    cache = dem.parent
    files = sorted(
        p
        for pat in ("dem_*.tif", "roads_*.gpkg", "buildings_*.gpkg")
        for p in cache.glob(pat)
    )
    if args.json:
        print(
            _dumps(
                {
                    "dem": str(dem),
                    "cache_dir": str(cache),
                    "roads": len(inputs.roads),
                    "buildings": len(inputs.buildings),
                    "crs": prov.get("crs"),
                    "bbox_wgs84": prov.get("bbox_wgs84", list(args.bbox)),
                    "provenance": prov,
                    "cache_files": [
                        {
                            "name": p.name,
                            "path": str(p),
                            "size_bytes": p.stat().st_size,
                        }
                        for p in files
                    ],
                }
            )
        )
        return EXIT_OK
    print(f"DEM: {dem}")
    print(f"roads: {len(inputs.roads)} segments")
    print(f"buildings: {len(inputs.buildings)}")
    print(f"CRS: {prov.get('crs', 'n/a')}")
    print(f"bbox (W,S,E,N): {prov.get('bbox_wgs84', list(args.bbox))}")
    print(f"fetched at: {prov.get('fetched_at', 'n/a')}")
    print(f"cache folder: {cache}")
    for p in files:
        print(f"  {p.name}  ({p.stat().st_size / 1e6:.2f} MB)")
    print("provenance:")
    print(_dumps(prov))
    return EXIT_OK


def _cmd_report(args: argparse.Namespace) -> int:
    from .viz.report import build_report

    bundles = _read_bundles(args.bundles, args.names)
    arg = next(iter(bundles.values())) if len(bundles) == 1 else bundles
    title = args.title or (next(iter(bundles)) if len(bundles) == 1 else None)
    path = build_report(arg, args.out, title=title)
    kind = "report" if len(bundles) == 1 else f"comparison of {len(bundles)} networks"
    print(f"wrote {kind}: {path}")
    return EXIT_OK


def _compare_text(comp) -> str:
    """Describe a Comparison in plain sentences."""
    m = comp.metrics
    sp = comp.spatial

    def metric(key: str, name: str):
        if key in m.index and name in m.columns:
            val = m.loc[key, name]
            return None if val != val else val.item() if hasattr(val, "item") else val
        return None

    lines = [f"Base: {comp.base}"]
    for name in comp.names:
        lines.append(
            f"{name}: {_fmt(metric('n_pipes', name), digits=0)} pipes, "
            f"{_fmt(metric('total_length_m', name), 'm', 0)} in total, "
            f"{_fmt(metric('n_pumping_stations', name), digits=0)} pumping and "
            f"{_fmt(metric('n_lifting_stations', name), digits=0)} lifting stations."
        )
    for name in comp.names:
        if name == comp.base:
            continue
        row = sp.loc[name]
        base_total = float(row["shared_length_m"]) + float(row["removed_length_m"])
        share = (
            100.0 * float(row["shared_length_m"]) / base_total if base_total else 0.0
        )
        delta = metric("total_length_m", name)
        base_len = metric("total_length_m", comp.base)
        diff = (
            f"{delta - base_len:+,.0f} m"
            if delta is not None and base_len is not None
            else "n/a"
        )
        lines.append(
            f"{name} against {comp.base}: shares {row['shared_length_m']:,.0f} m "
            f"({share:.0f}% of the base) of the same pipes, adds "
            f"{row['added_length_m']:,.0f} m, removes {row['removed_length_m']:,.0f} m; "
            f"{int(row['n_changed'])} of {int(row['n_shared'])} shared pipes change "
            f"diameter, type or direction; total length changes by {diff}."
        )
    if len(comp.config_diff):
        lines.append("Configuration differences:")
        for key, vals in comp.config_diff.iterrows():
            shown = ", ".join(f"{c}={vals[c]}" for c in comp.config_diff.columns)
            lines.append(f"  {key}: {shown}")
    return "\n".join(lines)


def _cmd_compare(args: argparse.Namespace) -> int:
    import dataclasses

    from .compare import compare

    bundles = _read_bundles(args.bundles, args.names)
    comp = compare(bundles, base=args.base, tolerance=args.tolerance)
    if args.json:
        if not args.with_geometry:
            comp = dataclasses.replace(comp, pipe_diff={})
        print(_dumps(comp.to_dict()))
    else:
        print(_compare_text(comp))
    return EXIT_OK


def _cmd_profile(args: argparse.Namespace) -> int:
    from .viz.profile import longitudinal_profile

    bundles = _read_bundles([args.bundle], None)
    bundle = next(iter(bundles.values()))
    profile = longitudinal_profile(bundle, args.node)
    if args.png:
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            from .viz.mpl import plot_profile
        except ImportError as exc:
            raise CliError(f"{_NO_PLOT} ({exc})", EXIT_ERROR) from exc
        fig, _ = plot_profile(profile, title=f"Longitudinal section from {args.node}")
        try:
            fig.savefig(args.png, dpi=150, bbox_inches="tight")
        finally:
            plt.close(fig)
    if args.json:
        print(_dumps(profile.to_dict()))
    else:
        segs = profile.segments
        print(
            f"profile from {args.node}: {len(segs)} pipes, "
            f"{profile.total_length:,.1f} m long"
        )
        if segs:
            print(f"  ends at node {segs[-1].to_node}")
        if args.png:
            print(f"  wrote {args.png}")
    return EXIT_OK


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

_EPILOG = """\
exit codes:
  0    success
  1    unexpected error (traceback with --debug)
  2    usage or argument error
  3    input or validation error (also a failed fetch)
  4    bounding box larger than --max-area
  5    --offline and the cache has no data for the box
  6    network not reachable (use --offline with a filled cache)
  130  interrupted (Ctrl-C)
"""


def _add_fetch_options(p: argparse.ArgumentParser, *, required_bbox: bool) -> None:
    g = p.add_argument_group("fetch options")
    g.add_argument(
        "--bbox",
        type=parse_bbox,
        metavar="W,S,E,N",
        required=required_bbox,
        help="bounding box in WGS84 degrees (lon,lat); fetches OSM data and a DEM",
    )
    g.add_argument(
        "--dem-source",
        default="copernicus",
        metavar="copernicus|PATH",
        help="DEM source: 'copernicus' (GLO-30, a surface model) or a local DEM "
        "file (default: copernicus)",
    )
    g.add_argument(
        "--crs", metavar="EPSG", help="projected CRS (default: UTM of the box)"
    )
    g.add_argument(
        "--cache-dir", metavar="DIR", help="cache folder (default: user cache)"
    )
    g.add_argument(
        "--max-area",
        type=float,
        metavar="KM2",
        help="area limit of the box (default 4)",
    )
    g.add_argument(
        "--offline", action="store_true", help="use cached data only; never download"
    )
    g.add_argument(
        "--retries",
        type=int,
        default=1,
        metavar="N",
        help="retries on network errors (default 1)",
    )
    g.add_argument(
        "--retry-wait",
        type=float,
        default=5.0,
        metavar="S",
        help="first wait between retries in s (default 5)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of the ``pysewer`` command."""
    parser = argparse.ArgumentParser(
        prog="pysewer",
        description="Design sewer networks from a DEM, roads and buildings.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="store_true", help="print the version and exit"
    )
    parser.add_argument(
        "--debug", action="store_true", help="show the traceback of unexpected errors"
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def add(name: str, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(
            name,
            help=help_,
            description=help_,
            epilog=_EPILOG,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        p.add_argument(
            "--debug",
            action="store_true",
            default=argparse.SUPPRESS,
            help="show the traceback of unexpected errors",
        )
        return p

    # run
    p = add("run", "Design a network and write the bundle, report and summary.")
    g = p.add_argument_group("inputs (files, or a --bbox to fetch)")
    g.add_argument("--dem", metavar="PATH", help="DEM raster (projected CRS)")
    g.add_argument("--roads", metavar="PATH", help="roads vector file")
    g.add_argument("--buildings", metavar="PATH", help="buildings vector file")
    _add_fetch_options(p, required_bbox=False)
    g = p.add_argument_group("sink")
    g.add_argument(
        "--sink",
        type=parse_sink,
        default="lowest",
        metavar="lowest|X,Y",
        help="'lowest' road node, or X,Y in the DEM CRS (default: lowest)",
    )
    g.add_argument(
        "--sink-crs", metavar="EPSG", help="CRS of an X,Y sink (for example 4326)"
    )
    g = p.add_argument_group("configuration overrides")
    g.add_argument("--tmax", type=float, metavar="M", help="maximum trench depth in m")
    g.add_argument("--tmin", type=float, metavar="M", help="minimum cover in m")
    g.add_argument(
        "--people-per-building",
        type=int,
        metavar="N",
        help="inhabitants per building (optimization.inhabitants_dwelling)",
    )
    g.add_argument(
        "--station-cost",
        type=float,
        metavar="M",
        help="cost of a lifting station in m of gravity pipe",
    )
    g.add_argument(
        "--set",
        action="append",
        metavar="SECTION.KEY=VALUE",
        help="any config value; VALUE is JSON if it parses, else a string. "
        "Repeatable; wins over the flags above",
    )
    g = p.add_argument_group("output")
    g.add_argument("--name", help="network name (default: stem of --out, or 'network')")
    g.add_argument(
        "--out",
        metavar="PATH",
        help="bundle output (gpkg file, or a directory for geojson/parquet)",
    )
    g.add_argument(
        "--format",
        choices=("gpkg", "geojson", "parquet"),
        help="bundle format (default: gpkg for a .gpkg path, else parquet)",
    )
    g.add_argument("--report", metavar="HTML", help="also write an HTML report")
    g.add_argument(
        "--progress",
        choices=("text", "jsonl", "none"),
        default="text",
        help="progress on stderr: text lines, JSON lines "
        "{stage, fraction, message}, or none (default: text)",
    )
    g.add_argument(
        "--json", action="store_true", help="print network_metrics as JSON on stdout"
    )
    p.set_defaults(func=_cmd_run)

    # fetch
    p = add("fetch", "Fetch roads, buildings and a DEM into the cache.")
    _add_fetch_options(p, required_bbox=True)
    p.add_argument(
        "--progress",
        choices=("text", "jsonl", "none"),
        default="text",
        help="progress on stderr (default: text)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="print one JSON object on stdout (no other text)",
    )
    p.set_defaults(func=_cmd_fetch)

    # report
    p = add("report", "Write an HTML report of one bundle, or a comparison of several.")
    p.add_argument("bundles", nargs="+", metavar="BUNDLE", help="bundle path(s)")
    p.add_argument("--out", required=True, metavar="HTML", help="output HTML file")
    p.add_argument("--title", help="report title")
    p.add_argument("--names", metavar="A,B,...", help="variant names, comma-separated")
    p.set_defaults(func=_cmd_report)

    # compare
    p = add("compare", "Compare two or more bundles.")
    p.add_argument("bundles", nargs="+", metavar="BUNDLE", help="bundle paths")
    p.add_argument("--names", metavar="A,B,...", help="variant names, comma-separated")
    p.add_argument("--base", metavar="NAME", help="base variant (default: the first)")
    p.add_argument(
        "--tolerance",
        type=float,
        default=0.01,
        metavar="M",
        help="pipe end point match distance (default 0.01)",
    )
    p.add_argument("--json", action="store_true", help="print the comparison as JSON")
    p.add_argument(
        "--with-geometry",
        action="store_true",
        help="with --json: include the pipe_diff GeoJSON",
    )
    p.set_defaults(func=_cmd_compare)

    # profile
    p = add("profile", "Show the longitudinal profile from a node to its sink.")
    p.add_argument("bundle", metavar="BUNDLE", help="bundle path")
    p.add_argument(
        "--node", required=True, metavar="NODE_ID", help="start node, e.g. N000057"
    )
    p.add_argument(
        "--png", metavar="PATH", help="draw the profile (needs the plot extra)"
    )
    p.add_argument("--json", action="store_true", help="print the profile as JSON")
    p.set_defaults(func=_cmd_profile)

    return parser


def _check_run_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    files = (args.dem, args.roads, args.buildings)
    try:
        parse_overrides(args.set)
    except ValueError as exc:
        parser.error(str(exc))
    if args.bbox is not None:
        if any(files):
            parser.error("use either --bbox or --dem/--roads/--buildings, not both")
    elif not all(files):
        parser.error("give --dem, --roads and --buildings, or give --bbox")
    elif args.offline or args.cache_dir or args.crs or args.max_area is not None:
        parser.error("--offline, --cache-dir, --crs and --max-area need --bbox")


def _report_network(exc: BaseException) -> int:
    print(f"error: {_unreachable(_network_failure(exc)[1])}", file=sys.stderr)
    return EXIT_NETWORK


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the ``pysewer`` command.

    Parameters
    ----------
    argv : sequence of str, optional
        Arguments without the program name. Default ``sys.argv[1:]``.

    Returns
    -------
    int
        The exit code (see the module docstring).
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        from importlib.metadata import PackageNotFoundError, version

        try:
            print(f"pysewer {version('pysewer')}")
        except PackageNotFoundError:
            print("pysewer (version unknown: not installed)")
        return EXIT_OK
    if not getattr(args, "func", None):
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    if args.command == "run":
        _check_run_args(parser, args)

    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPT
    except CliError as exc:
        if args.debug:
            raise
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except (ValueError, FileNotFoundError) as exc:
        if args.debug:
            raise
        if _exit_code_for(exc) is None and _network_failure(exc)[0]:
            return _report_network(exc)
        message = " ".join(str(exc).split()) or type(exc).__name__
        print(f"error: {message}", file=sys.stderr)
        return _exit_code_for(exc) or EXIT_INPUT
    except Exception as exc:
        if args.debug:
            raise
        if _network_failure(exc)[0]:
            return _report_network(exc)
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
