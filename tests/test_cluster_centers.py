# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""``Buildings.cluster_centers``: one center per cluster, at the mean of its buildings."""

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

import pysewer

CRS = "EPSG:32632"
MAX_LEN = 30.0


def _buildings(points):
    road = gpd.GeoDataFrame(geometry=[LineString([(-100, 0), (1000, 0)])], crs=CRS)
    roads = pysewer.Roads(road)
    gdf = gpd.GeoDataFrame(geometry=[Point(p) for p in points], crs=CRS)
    return pysewer.Buildings(gdf, roads_obj=roads)


def _xy(centers):
    return sorted((round(p.x, 6), round(p.y, 6)) for p in centers.geometry)


# all 100 m from the road (> MAX_LEN): a 4-building cluster listed in "bow-tie"
# order, a lone building, then a 2-building cluster
POINTS = [
    (0, 100), (10, 110), (10, 100), (0, 110),
    (500, 100),
    (900, 100), (910, 100),
]


def test_one_center_per_cluster():
    # 0.3.1 appended a center per *building*: 4 + 1 + 2 = 7 entries
    centers = _buildings(POINTS).cluster_centers(MAX_LEN)
    assert len(centers) == 2
    assert len(set(_xy(centers))) == 2


def test_center_is_mean_of_buildings():
    # 0.3.1 built Polygon(points) in table order: a self-intersecting bow-tie
    # whose centroid is not (5, 105)
    centers = _buildings(POINTS).cluster_centers(MAX_LEN)
    assert _xy(centers) == [(5.0, 105.0), (905.0, 100.0)]


def test_lone_building_gets_no_center():
    # 0.3.1 added another copy of the previous cluster's center for it
    lone_last = [(0, 100), (10, 100), (500, 100)]
    centers = _buildings(lone_last).cluster_centers(MAX_LEN)
    assert _xy(centers) == [(5.0, 100.0)]


def test_building_exactly_at_max_connection_length():
    # 0.3.1 selected "> MAX_LEN" for clustering but wrote labels back with
    # ">= MAX_LEN": a length mismatch error
    pts = [(300, MAX_LEN), (0, 100), (10, 100)]
    centers = _buildings(pts).cluster_centers(MAX_LEN)
    assert _xy(centers) == [(5.0, 100.0)]


@pytest.mark.parametrize("points", [[], [(0, 5)], [(0, 100)]])
def test_fewer_than_two_far_buildings(points):
    centers = _buildings(points + [(200, 5)]).cluster_centers(MAX_LEN)
    assert len(centers) == 0
