# SPDX-License-Identifier: GPL-3.0-only
"""
Single entry point that turns input data into a designed network bundle.

``generate`` runs the full chain: config -> :class:`~pysewer.ModelDomain` ->
sink -> connection graph -> :func:`~pysewer.route_and_design` ->
:class:`~pysewer.bundle.NetworkBundle`. The CLI and the plugin both call it.
"""

from __future__ import annotations

import copy
import dataclasses
import logging
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd

from .bundle import NetworkBundle
from .config import manager
from .config.settings import deep_merge, dict_to_config, validate_config_values
from .optimization import route_and_design
from .preprocessing import ModelDomain

__all__ = ["Inputs", "generate"]

logger = logging.getLogger(__name__)
Progress = Callable[[str, float, str], None]


@dataclass(frozen=True)
class Inputs:
    """
    Input data of one run.

    Parameters
    ----------
    dem : str or os.PathLike
        Path to the DEM raster. Its CRS is the working CRS (must be projected).
    roads, buildings : str, os.PathLike or geopandas.GeoDataFrame
        Vector layers, as file paths or as data frames.
    provenance : dict
        Optional keys ``dem_source``, ``fetched_at``, ``bbox_wgs84``, and
        per-layer dicts ``dem``/``roads``/``buildings`` (``source``, ``path``,
        ``url``). Other keys go to ``provenance["extra"]`` of the bundle.
    """

    dem: str | os.PathLike
    roads: str | os.PathLike | gpd.GeoDataFrame
    buildings: str | os.PathLike | gpd.GeoDataFrame
    provenance: dict = field(default_factory=dict)


def _report(
    progress: Progress | None, stage: str, fraction: float, message: str
) -> None:
    logger.info("[%s] %s", stage, message)
    if progress is not None:
        progress(stage, fraction, message)


def _layer_source(layer) -> dict:
    if isinstance(layer, (str, os.PathLike)):
        return {"source": "file", "path": str(layer)}
    return {"source": "GeoDataFrame"}


def _dem_crs(dem_path):
    import rasterio

    with rasterio.open(dem_path) as src:
        return src.crs


def _align_layer(layer, label: str, dem_crs, notes: list[str]):
    """Check a vector layer and return it in the DEM CRS (reprojected if needed)."""
    if isinstance(layer, (str, os.PathLike)):
        if not Path(layer).exists():
            raise ValueError(f"The {label} file does not exist: {layer}")
        try:
            gdf = gpd.read_file(layer)
        except Exception as exc:  # unreadable file
            raise ValueError(f"Cannot read the {label} file {layer}: {exc}") from exc
    elif isinstance(layer, gpd.GeoDataFrame):
        gdf = layer
    else:
        raise ValueError(
            f"The {label} input must be a path or a GeoDataFrame, got {type(layer).__name__}."
        )
    if gdf.crs is None:
        notes.append(f"{label} has no CRS; the DEM CRS is assumed")
        logger.warning(notes[-1])
        return gdf.set_crs(dem_crs)  # ModelDomain needs a CRS on every layer
    if not gdf.crs.equals(dem_crs):
        notes.append(
            f"{label} reprojected from {gdf.crs.to_string()} to {dem_crs.to_string()}"
        )
        logger.info(notes[-1])
        return gdf.to_crs(dem_crs)
    return layer


class _HeartbeatHandler(logging.Handler):
    """Forward engine log records as progress calls, creeping towards ``end``."""

    def __init__(self, progress: Progress, start: float, end: float):
        super().__init__(level=logging.INFO)
        self._progress = progress
        self._fraction = start
        self._end = end

    def emit(self, record: logging.LogRecord) -> None:
        # each record closes 5 % of the remaining gap, so the end is never reached
        self._fraction += (self._end - self._fraction) * 0.05
        message = record.getMessage().strip().replace("\n", " ")
        if len(message) > 120:
            message = message[:117] + "..."
        self._progress("route_design", self._fraction, message)


@contextmanager
def _heartbeat(progress: Progress | None, start: float, end: float) -> Iterator[None]:
    """Forward pysewer engine log records as progress calls for the block.

    An exception raised by ``progress`` propagates out of the engine call and
    aborts the run. The logger level is restored on exit. While the block
    runs, the "pysewer" logger is at least at INFO, so INFO records also
    reach the other handlers of that logger.
    """
    if progress is None:
        yield
        return
    pylogger = logging.getLogger("pysewer")
    handler = _HeartbeatHandler(progress, start, end)
    old_level = pylogger.level
    if not pylogger.isEnabledFor(logging.INFO):
        pylogger.setLevel(logging.INFO)
    pylogger.addHandler(handler)
    try:
        yield
    finally:
        pylogger.removeHandler(handler)
        pylogger.setLevel(old_level)


def _effective_config(overrides: Mapping | None):
    """Return the current config with ``overrides`` merged on top of it."""
    current = manager.get_config()
    if not overrides:
        return current
    valid = [f.name for f in dataclasses.fields(current)]
    unknown = [k for k in overrides if k not in valid]
    if unknown:
        raise ValueError(
            f"Unknown config section(s) in overrides: {unknown}. "
            f"Valid sections: {valid}."
        )
    merged = deep_merge(copy.deepcopy(dict(overrides)), dataclasses.asdict(current))
    try:
        config = dict_to_config(merged)
        validate_config_values(config)
    except (TypeError, KeyError) as exc:
        raise ValueError(
            f"Invalid config overrides {dict(overrides)!r}: {exc}. "
            "Use nested keys such as {'optimization': {'tmax': 3}}."
        ) from exc
    return config


def generate(
    inputs: Inputs,
    *,
    sink: str | tuple[float, float] = "lowest",
    sink_crs: str | int | None = None,
    overrides: Mapping | None = None,
    name: str = "network",
    progress: Progress | None = None,
) -> NetworkBundle:
    """
    Design a sewer network from DEM, roads and buildings.

    Parameters
    ----------
    inputs : Inputs
        DEM, roads, buildings and provenance.
    sink : "lowest" or (x, y), default "lowest"
        ``"lowest"`` puts the sink at the lowest road node
        (:meth:`ModelDomain.set_sink_lowest`). A tuple is the sink location in
        the DEM CRS, or in ``sink_crs`` if given.
    sink_crs : str or int, optional
        CRS of a tuple sink (e.g. 4326 for lon, lat). The point is reprojected
        to the DEM CRS first.
    overrides : mapping, optional
        Nested config values, e.g. ``{"optimization": {"tmax": 3}}``. They are
        applied on top of the current config for this run only. The previous
        config returns afterwards, also on error. The bundle metadata records
        the config of the run.
    name : str
        Bundle name.
    progress : callable, optional
        ``progress(stage, fraction, message)`` with stage ``load``, ``connect``,
        ``route_design`` or ``bundle`` and fraction from 0 to 1 (monotone).
        During ``route_design`` it is also called for each engine log record
        (fraction between 0.55 and 0.9). The callback can cancel the run: an
        exception it raises aborts the run, propagates to the caller, and the
        config is restored.

    Returns
    -------
    NetworkBundle

    Raises
    ------
    ValueError
        For a missing file, an invalid sink, invalid overrides or a missing CRS.
    """
    if not isinstance(inputs, Inputs):
        raise ValueError("inputs must be a pysewer.pipeline.Inputs object.")
    if isinstance(sink, str):
        if sink != "lowest":
            raise ValueError(f"sink must be 'lowest' or an (x, y) tuple, got {sink!r}.")
    else:
        try:
            x, y = (float(v) for v in sink)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"sink must be 'lowest' or an (x, y) tuple, got {sink!r}."
            ) from exc
    dem_path = Path(inputs.dem)
    if not dem_path.exists():
        raise ValueError(f"The DEM file does not exist: {dem_path}")

    config = _effective_config(overrides)  # validate before any state change
    with manager.use_config(config):
        _report(progress, "load", 0.0, "Loading DEM, roads and buildings")
        dem_crs = _dem_crs(dem_path)
        if dem_crs is None:
            raise ValueError(f"The DEM has no CRS: {dem_path}")
        if not dem_crs.is_projected:
            raise ValueError(
                f"The DEM CRS must be projected (metres), got {dem_crs.to_string()}. "
                "Reproject the DEM first."
            )
        notes: list[str] = []
        roads = _align_layer(inputs.roads, "roads", dem_crs, notes)
        buildings = _align_layer(inputs.buildings, "buildings", dem_crs, notes)
        md = ModelDomain(str(dem_path), roads, buildings)
        _report(progress, "load", 0.25, "Input data loaded")

        if isinstance(sink, str):
            md.set_sink_lowest()
        else:
            if sink_crs is not None:
                from pyproj import CRS, Transformer

                x, y = Transformer.from_crs(
                    CRS.from_user_input(sink_crs), dem_crs, always_xy=True
                ).transform(x, y)
            md.add_sink((x, y))
        sinks = md.get_sinks()
        if not sinks:
            raise ValueError("No sink could be placed; give an explicit (x, y) sink.")

        _report(progress, "connect", 0.3, "Generating the connection graph")
        cg = md.generate_connection_graph()
        _report(progress, "connect", 0.5, "Connection graph ready")

        _report(progress, "route_design", 0.55, "Routing and designing the network")
        with _heartbeat(progress, 0.55, 0.9):
            G = route_and_design(cg, sinks)
        _report(progress, "route_design", 0.9, "Network designed")

        _report(progress, "bundle", 0.92, "Building the network bundle")
        prov = dict(inputs.provenance or {})
        for key, layer in (
            ("dem", inputs.dem),
            ("roads", inputs.roads),
            ("buildings", inputs.buildings),
        ):
            prov[key] = {**_layer_source(layer), **(prov.get(key) or {})}
        if notes:
            prov["crs_notes"] = notes
        prov["run"] = {  # lands in provenance["extra"]["run"] of the bundle
            "sink": "lowest" if isinstance(sink, str) else [x, y],
            "sink_crs": None if sink_crs is None else str(sink_crs),
            "overrides": copy.deepcopy(dict(overrides)) if overrides else {},
        }
        bundle = NetworkBundle.from_graph(G, inputs=prov, name=name, sinks=sinks)
        _report(progress, "bundle", 1.0, "Done")
        return bundle
