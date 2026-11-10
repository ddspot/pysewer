# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""
Since the profile and trench_depth_profile are list of tuples, they cannot be exported 
to a shapefile or GeoPackage directly using the native GeoPandas function (to_file). 
This module contains functions to convert the list of tuples to JSON strings, 
which can then be exported to a shapefile or GeoPackage. 
The option saving the data as a parquet file is added.

"""

import json
import logging

import fiona
import geopandas as gpd
import numpy as np
import pandas as pd
import pandas.api.types as ptypes
from fiona.collection import CRS

from .config.manager import get_config

logger = logging.getLogger(__name__)


def map_dtype_to_fiona(dtype):
    """Map pandas data type to Fiona/OGR data type."""
    if "int" in dtype:
        return "int"
    elif "float" in dtype:
        return "float"
    elif "bool" in dtype:
        return "bool"
    else:
        return "str"


def is_list_of_tuples(column):
    """Check if a pandas Series contains lists of tuples."""
    return all(
        isinstance(item, (list, tuple))
        and all(isinstance(sub_item, tuple) for sub_item in item)
        for item in column
    )

def is_list(column):
    """Check if a pandas Series contains lists."""
    return all(isinstance(item, list) for item in column)

def tuple_list_to_json(tuple_list, decimals: int | None = None):
    """Serialize a list of tuples to a JSON string, optionally rounding floats."""
    if decimals is not None:

        def _round(value):
            return round(value, decimals) if isinstance(value, float) else value

        tuple_list = [
            tuple(_round(v) for v in item)
            if isinstance(item, (list, tuple))
            else _round(item)
            for item in tuple_list
        ]
    return json.dumps(tuple_list)


def round_float_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Round float attribute columns according to ``export.round_decimals``.

    The "default" entry covers columns without an explicit setting; a
    null/None config disables rounding entirely. Keeps exported files free of
    float noise (7+ decimal places) that downstream tools such as Elan would
    otherwise have to clean up themselves.
    """
    decimals = get_config().export.round_decimals or {}
    default = decimals.get("default")
    for col in gdf.columns:
        if col == "geometry":
            continue
        n_digits = decimals.get(col, default)
        if n_digits is not None and ptypes.is_float_dtype(gdf[col]):
            gdf[col] = gdf[col].round(n_digits)
    return gdf


def generate_schema(gdf: gpd.GeoDataFrame):
    """
    Generate a schema based on the GeoDataFrame.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        The GeoDataFrame to generate the schema from.

    Returns
    -------
    dict
        The schema dictionary.

    """
    schema = {
        "geometry": gdf.geometry.type.iloc[0],
        "properties": {
            col: map_dtype_to_fiona(gdf[col].dtype.name)
            for col in gdf.columns
            if col != "geometry"
        },
    }
    return schema


def _is_missing(value) -> bool:
    """True for None and NaN-like scalars; lists and other containers are never missing."""
    if isinstance(value, (list, tuple, dict, np.ndarray)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _field_type(values: list) -> str:
    """Fiona field type for the non-missing values of a column."""
    if values and all(isinstance(v, (bool, np.bool_)) for v in values):
        return "bool"
    numbers = [v for v in values if not isinstance(v, (bool, np.bool_))]
    if values and len(numbers) == len(values):
        if all(isinstance(v, (int, np.integer)) for v in values):
            return "int"
        if all(isinstance(v, (int, float, np.integer, np.floating)) for v in values):
            return "float"
    return "str"


def _properties(gdf: gpd.GeoDataFrame, decimals: int | None = None) -> tuple[dict, dict]:
    """Attribute values and field types ready for fiona.

    A column's type comes from its non-missing values, so a column of
    ``True`` and NaN is a bool column. Missing values are written as NULL
    (not as the string "nan"), and lists (profiles, violation lists) as JSON.

    Returns
    -------
    tuple of dict
        ``{column: list of values}`` and ``{column: fiona type}``.
    """
    values, types = {}, {}
    for col in gdf.columns:
        if col == gdf.geometry.name:
            continue
        column = gdf[col].tolist()
        present = [v for v in column if not _is_missing(v)]
        if present and all(isinstance(v, (list, tuple)) for v in present):
            kind = "str"
        else:
            kind = _field_type(present)

        def convert(v, kind=kind):
            if _is_missing(v):
                return None
            if isinstance(v, (list, tuple)):
                return tuple_list_to_json(v, decimals=decimals)
            return {"bool": bool, "int": int, "float": float, "str": str}[kind](v)

        values[col] = [convert(v) for v in column]
        types[col] = kind
    return values, types


def _mixed_lists_to_text(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Columns mixing lists and scalars (e.g. road attributes of merged edges)
    as text, lists as JSON; Parquet cannot store such a column."""
    for col in gdf.columns:
        if col == gdf.geometry.name or gdf[col].dtype != "object":
            continue
        present = [v for v in gdf[col] if not _is_missing(v)]
        lists = [isinstance(v, (list, tuple)) for v in present]
        if any(lists) and not all(lists):
            gdf[col] = [
                None if _is_missing(v)
                else json.dumps(v, default=str) if isinstance(v, (list, tuple))
                else str(v)
                for v in gdf[col]
            ]
    return gdf


def _write_features(dst, gdf: gpd.GeoDataFrame, values: dict, skip_errors: bool = False):
    for i, geom in enumerate(gdf.geometry):
        feature = {
            "geometry": geom.__geo_interface__,
            "properties": {col: column[i] for col, column in values.items()},
        }
        try:
            dst.write(feature)
        except Exception as e:
            if not skip_errors:
                raise
            logger.warning("Error writing feature to SHP file: %s", e)


def _to_wkt(crs) -> str | None:
    """WKT for a pyproj/fiona CRS, an EPSG code or string, or None."""
    if crs is None:
        return None
    if hasattr(crs, "to_wkt"):
        return crs.to_wkt()
    return CRS.from_user_input(crs).to_wkt()


def write_gdf_to_gpkg(gdf: gpd.GeoDataFrame, filepath: str, layer: str | None=None, crs: CRS | None=None):
    """
    Write a GeoDataFrame to a GeoPackage (GPKG) file using Fiona, converting lists of tuples to JSON strings.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        The GeoDataFrame to be written to the GPKG file.
    filepath : str
        The file path to the GPKG file.
    layer: str (optional)
        The layer name inside the GPKG file. If no layer name is specified the file name is used.
    crs: CRS (optional)
        The layer CRS. Defaults to the CRS of ``gdf``.

    Returns
    -------
    None

    Notes
    -----
    This function converts any columns with list of tuples to JSON strings before writing to the GPKG file.
    Float columns and profile tuples are rounded according to ``export.round_decimals``.
    """
    gdf = round_float_columns(gdf.copy())
    profile_decimals = (get_config().export.round_decimals or {}).get("default")
    values, types = _properties(gdf, profile_decimals)

    schema = {"geometry": gdf.geometry.type.iloc[0], "properties": types}
    crs = crs if crs is not None else gdf.crs
    crs_wkt = _to_wkt(crs)
    with fiona.open(
        filepath, mode="w", driver="GPKG", schema=schema, layer=layer, crs_wkt=crs_wkt
    ) as dst:
        _write_features(dst, gdf, values)


def write_gdf_to_shp(gdf: gpd.GeoDataFrame, filepath: str):
    """
    Write a GeoDataFrame to an ESRI Shapefile (SHP) file using Fiona, converting lists of tuples to JSON strings.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        The GeoDataFrame to be written to the SHP file.
    filepath : str
        The file path to save the SHP file.

    Returns
    -------
    None

    Notes
    -----
    This function converts any columns in the GeoDataFrame that contain lists of tuples to JSON strings before writing to the SHP file.
    Float columns and profile tuples are rounded according to ``export.round_decimals``.
    """
    gdf = round_float_columns(gdf.copy())
    profile_decimals = (get_config().export.round_decimals or {}).get("default")
    values, types = _properties(gdf, profile_decimals)
    schema = {"geometry": gdf.geometry.type.iloc[0], "properties": types}
    crs_wkt = _to_wkt(gdf.crs)
    with fiona.open(
        filepath, mode="w", driver="ESRI Shapefile", schema=schema, crs_wkt=crs_wkt
    ) as dst:
        _write_features(dst, gdf, values, skip_errors=True)


def export_sewer_network(
    gdf: gpd.GeoDataFrame,
    filepath: str,
    file_format: str | None = None,
):
    """
    Export a sewer network GeoDataFrame to a file.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        A GeoDataFrame containing the sewer network.
    filepath : str
        The path to the file to which the sewer network should be exported.
    file_format : str
        The file format to which the sewer network should be exported. Default is 'gpkg' (GeoPackage).
        Currently supported formats are 'gpkg' (GeoPackage), 'shp' (ESRI Shapefile) and Geoparquet 'parquet'.

    Raises
    ------
    ValueError
        If the file format is not supported.

    Returns
    -------
    None
    """
    config = get_config()
    if file_format is None:
        file_format = config.export.file_format

    supported_formats = ["gpkg", "shp", "parquet"]
    if file_format not in supported_formats:
        raise ValueError(f"File format {file_format} is not supported.")

    if file_format == "gpkg":
        write_gdf_to_gpkg(gdf, filepath)
    elif file_format == "shp":
        write_gdf_to_shp(gdf, filepath)
    elif file_format == "parquet":
        gdf = _mixed_lists_to_text(round_float_columns(gdf.copy()))
        gdf.to_parquet(
            filepath, index=False
        )  # index set false to avoid AttributeError: module 'pandas' has no attribute 'Int64Index'
    logger.info("Exported sewer network to %s", filepath)
