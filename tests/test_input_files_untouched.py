# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""pysewer reads the DEM file but never writes to it."""

import hashlib

import numpy as np
import rasterio
from pyproj import CRS
from rasterio.transform import from_origin
from shapely.geometry import Point

import pysewer


def _md5(path):
    return hashlib.md5(path.read_bytes()).hexdigest()


def _dem_with_nodata(path):
    data = np.full((10, 10), 50.0, dtype="float32")
    data[0, 0] = -9999.0  # declared nodata
    with rasterio.open(
        path, "w", driver="GTiff", height=10, width=10, count=1, dtype="float32",
        crs="EPSG:32632", transform=from_origin(500000, 5000100, 10, 10), nodata=-9999.0,
    ) as dst:
        dst.write(data, 1)
    return path


def test_remove_nodata_keeps_file(tmp_path):
    path = _dem_with_nodata(tmp_path / "dem.tif")
    before = _md5(path)
    dem = pysewer.DEM(path)  # calls remove_nodata(fill_value=0)
    assert _md5(path) == before
    with rasterio.open(path) as src:
        assert src.nodata == -9999.0
    # the in-memory raster has the nodata cells filled, as before
    assert dem.raster.nodata == 0
    assert dem.raster.read(1)[0, 0] == 0
    assert dem.get_elevation(Point(500055, 5000055)) == 50


def test_reproject_dem_keeps_file(tmp_path):
    path = _dem_with_nodata(tmp_path / "dem.tif")
    before = _md5(path)
    dem = pysewer.DEM(path)
    dem.reproject_dem(CRS.from_epsg(4326))
    assert _md5(path) == before
    assert dem.get_crs.to_epsg() == 4326
    assert dem.get_elevation(Point(9.0007, 45.15397)) == 50


def test_model_domain_keeps_dem_file(tmp_path):
    # the test DEM with one corner cell turned into declared nodata
    path = tmp_path / "dem.tif"
    with rasterio.open("tests/test_data/dem.tif") as src:
        data = src.read()
        meta = src.meta.copy()
    data[:, 0, 0] = -9999
    meta.update(nodata=-9999)
    with rasterio.open(path, "w", **meta) as dst:
        dst.write(data)
    before = _md5(path)
    pysewer.ModelDomain(
        path, "tests/test_data/roads_clipped.shp", "tests/test_data/buildings_clipped.shp"
    )
    assert _md5(path) == before
