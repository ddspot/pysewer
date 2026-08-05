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

import fiona
import geopandas as gpd
import pandas.api.types as ptypes
from fiona.collection import CRS

from .config.manager import get_config


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
        The layer CRS

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
    # Convert only columns with list of tuples to JSON strings
    for col in gdf.columns:
        if gdf[col].dtype == "object" and (
            is_list_of_tuples(gdf[col]) or is_list(gdf[col])
        ):
            gdf[col] = gdf[col].apply(tuple_list_to_json, decimals=profile_decimals)

    # Define the schema based on the GeoDataFrame
    schema = generate_schema(gdf)

    # Open a new GPKG file in write mode
    with fiona.open(filepath, mode="w", driver="GPKG", schema=schema, layer=layer, crs=crs) as dst:
        for _, row in gdf.iterrows():
            feature = {
                "geometry": row["geometry"].__geo_interface__,
                "properties": {
                    col: row[col] for col in gdf.columns if col != "geometry"
                },
            }
            dst.write(feature)


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
    # Convert only columns with list of tuples to JSON strings
    for col in gdf.columns:
        if gdf[col].dtype == "object" and is_list_of_tuples(gdf[col]):
            gdf[col] = gdf[col].apply(tuple_list_to_json, decimals=profile_decimals)

    # Define the schema based on the GeoDataFrame
    schema = generate_schema(gdf)

    # Open a new SHP file in write mode
    with fiona.open(filepath, mode="w", driver="ESRI Shapefile", schema=schema) as dst:
        for _, row in gdf.iterrows():
            feature = {
                "geometry": row["geometry"].__geo_interface__,
                "properties": {
                    col: row[col] for col in gdf.columns if col != "geometry"
                },
            }
            try:
                dst.write(feature)
            except Exception as e:
                print(f"Error writing feature to SHP file: {e}")


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
        gdf = round_float_columns(gdf.copy())
        gdf.to_parquet(
            filepath, index=False
        )  # index set false to avoid AttributeError: module 'pandas' has no attribute 'Int64Index'
    print(f"Successfully exported sewer network to {filepath}.")
