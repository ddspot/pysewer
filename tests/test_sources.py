# SPDX-License-Identifier: GPL-3.0-only
"""Tests for pysewer.sources: input fetching with OSM and DEM access mocked."""

import json
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.errors import RasterioIOError
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, Polygon

from pysewer import sources
from pysewer.sources import (
    COPERNICUS_ATTRIBUTION,
    COPERNICUS_DERIVED_ATTRIBUTION,
    COPERNICUS_URL_TEMPLATE,
    DEFAULT_HIGHWAY,
    DEM_SOURCES,
    OSM_ATTRIBUTION,
    AreaTooLargeError,
    FetchError,
    OfflineCacheMissError,
    fetch_inputs,
)

REPO = Path(__file__).resolve().parents[1]
LEIPZIG = (12.30, 51.31, 12.31, 51.315)


# ---------------------------------------------------------------- helpers
def _elev(lon, lat):
    """Planar test surface, linear in lon/lat so bilinear resampling is exact."""
    return 100.0 + 100.0 * (np.asarray(lon) - 12.0) + 50.0 * (np.asarray(lat) - 51.0)


def _write_tile(path, lat, lon, step=0.002):
    n = round(1 / step)
    # pixel centres at lon + (j + 0.5) * step ; row 0 is the northern edge
    j = np.arange(n)
    lons = lon + (j + 0.5) * step
    lats = lat + 1 - (j + 0.5) * step
    data = _elev(lons[None, :], lats[:, None]).astype("float32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=n,
        width=n,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(lon, lat + 1, step, step),
    ) as ds:
        ds.write(data, 1)


class FakeTiles:
    """Redirect Copernicus URLs to local tiles; other opens pass through."""

    def __init__(self, monkeypatch, tmp_path, tiles):
        self.opened = []
        self.files = {}
        for lat, lon in tiles:
            f = tmp_path / f"tile_{lat}_{lon}.tif"
            _write_tile(f, lat, lon)
            self.files[sources._tile_url(lat, lon)] = f
        real_open = rasterio.open
        outer = self

        def fake_open(fp, *args, **kwargs):
            fp_s = str(fp)
            if fp_s.startswith("https://copernicus-dem-30m"):
                outer.opened.append(fp_s)
                if fp_s not in outer.files:
                    raise RasterioIOError(f"HTTP response code: 404 for {fp_s}")
                return real_open(outer.files[fp_s])
            return real_open(fp, *args, **kwargs)

        monkeypatch.setattr(rasterio, "open", fake_open)


class FakeOSM:
    """Replace the osmnx calls used by sources with synthetic data."""

    def __init__(self, monkeypatch, bbox=LEIPZIG):
        import osmnx as ox

        self.calls = {"graph": 0, "gdfs": 0, "features": 0}
        self.graph_kwargs = None
        self.graph_args = None
        self.cache_folder_seen = None
        self.features_args = None
        w, s, e, n = bbox
        cx, cy = (w + e) / 2, (s + n) / 2
        d = 0.001
        self.edges = gpd.GeoDataFrame(
            {
                "osmid": [[11, 12], 13],
                "highway": [["residential", "tertiary"], "residential"],
                "length": [50.0, 60.0],
            },
            geometry=[
                LineString([(cx - d, cy), (cx, cy)]),
                LineString([(cx, cy), (cx, cy + d)]),
            ],
            crs=4326,
        )
        self.edges.index = [(1, 2, 0), (2, 3, 0)]
        sq = lambda x, y: Polygon(  # noqa: E731
            [(x, y), (x + 1e-4, y), (x + 1e-4, y + 1e-4), (x, y + 1e-4)]
        )
        feats = gpd.GeoDataFrame(
            {"building": ["yes", "house", "yes"]},
            geometry=[sq(cx, cy), sq(cx + 3e-4, cy), Point(cx, cy)],
            crs=4326,
        )
        import pandas as pd

        feats.index = pd.MultiIndex.from_tuples(
            [("way", 101), ("way", 102), ("node", 103)]
        )
        self.feats = feats
        outer = self

        def graph_from_bbox(*a, **k):
            outer.calls["graph"] += 1
            outer.graph_args, outer.graph_kwargs = a, k
            outer.cache_folder_seen = ox.settings.cache_folder
            return "GRAPH"

        def graph_to_gdfs(g, **k):
            outer.calls["gdfs"] += 1
            assert g == "GRAPH"
            return outer.edges

        def features_from_bbox(*a, **k):
            outer.calls["features"] += 1
            outer.features_args = (a, k)
            return outer.feats

        monkeypatch.setattr(ox, "graph_from_bbox", graph_from_bbox)
        monkeypatch.setattr(ox, "graph_to_gdfs", graph_to_gdfs)
        monkeypatch.setattr(ox.convert, "to_undirected", lambda g: g)
        monkeypatch.setattr(ox, "features_from_bbox", features_from_bbox)

    @property
    def total(self):
        return sum(self.calls.values())


@pytest.fixture
def osm(monkeypatch):
    return FakeOSM(monkeypatch)


@pytest.fixture
def no_sleep(monkeypatch):
    import time

    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    return slept


@pytest.fixture
def tiles(monkeypatch, tmp_path_factory):
    return FakeTiles(
        monkeypatch, tmp_path_factory.mktemp("tiles"), [(51, 12), (51, 13)]
    )


# ------------------------------------------------------- bbox validation
@pytest.mark.parametrize(
    "bbox",
    [
        (12.31, 51.31, 12.30, 51.315),  # west > east
        (12.30, 51.315, 12.31, 51.31),  # south > north
        (12.30, 51.31, 12.30, 51.315),  # zero width
        (-181, 0, 0, 1),
        (0, 0, 181, 1),
        (0, -91, 1, 0),
        (0, 0, 1, 91),
        (float("nan"), 0, 1, 1),
        (0, 0, float("inf"), 1),
        (1, 2, 3),
        "abcd",
        None,
    ],
)
def test_bad_bbox_raises_value_error_and_creates_no_cache(bbox, tmp_path):
    cache = tmp_path / "cache"
    with pytest.raises(ValueError):
        fetch_inputs(bbox, cache_dir=cache)
    assert not cache.exists()


def test_area_guard_matches_hand_computed_area():
    # Spherical estimate, R = 6371 km, phi = 51.3125 deg, cos(phi) = 0.62547:
    # width  = 0.01 deg * pi/180 * 6371 * 0.62547 = 0.6955 km
    # height = 0.005 deg * pi/180 * 6371          = 0.5560 km
    # area   = 0.3867 km2 (ellipsoid differs by < 1 %)
    assert sources._validate_bbox(LEIPZIG, 0.3867 * 1.01) == LEIPZIG
    with pytest.raises(AreaTooLargeError, match="km2") as ei:
        sources._validate_bbox(LEIPZIG, 0.3867 * 0.99)
    assert "0.39 km2" in str(ei.value)


def test_default_area_limit_is_four_km2(tmp_path):
    # 0.02 x 0.02 deg at 51 N is about 1.5 x 2.2 = 3.5 km2: allowed;
    # 0.03 x 0.03 deg is about 7.8 km2: refused.
    sources._validate_bbox((12.30, 51.30, 12.32, 51.32), 4.0)
    with pytest.raises(AreaTooLargeError, match="limit") as ei:
        fetch_inputs((12.30, 51.30, 12.33, 51.33), cache_dir=tmp_path / "c")
    assert isinstance(ei.value, FetchError) and isinstance(ei.value, ValueError)


def test_non_projected_crs_rejected(tmp_path):
    with pytest.raises(ValueError, match="projected"):
        fetch_inputs(LEIPZIG, cache_dir=tmp_path, crs=4326)


# ------------------------------------------------------------ tile naming
@pytest.mark.parametrize(
    "lat, lon, token",
    [
        (51, 12, "N51_00_E012_00"),
        (0, 0, "N00_00_E000_00"),
        (-1, -1, "S01_00_W001_00"),
        (-34, -58, "S34_00_W058_00"),
        (5, -7, "N05_00_W007_00"),
        (-8, 179, "S08_00_E179_00"),
    ],
)
def test_tile_url_naming(lat, lon, token):
    url = sources._tile_url(lat, lon)
    assert url == (
        "https://copernicus-dem-30m.s3.amazonaws.com/"
        f"Copernicus_DSM_COG_10_{token}_DEM/Copernicus_DSM_COG_10_{token}_DEM.tif"
    )
    assert COPERNICUS_URL_TEMPLATE.startswith("https://copernicus-dem-30m")


def test_tile_origins_single_tile():
    assert sources._tile_origins((12.2, 51.2, 12.4, 51.4), 0, 0) == [(51, 12)]


def test_tile_origins_2x2():
    got = sources._tile_origins((12.9, 51.9, 13.1, 52.1), 0, 0)
    assert sorted(got) == [(51, 12), (51, 13), (52, 12), (52, 13)]


def test_tile_origins_margin_pulls_in_neighbour():
    # box edge 12.9995 + margin 0.001 reaches lon 13.0005, so tile 13 is needed
    got = sources._tile_origins((12.5, 51.5, 12.9995, 51.6), 0.001, 0.001)
    assert sorted(got) == [(51, 12), (51, 13)]


def test_tile_origins_cross_zero_and_negative():
    # lon -0.2..0.2 -> tiles W001 (floor -1) and E000 (floor 0)
    # lat -0.2..0.2 -> tiles S01 (floor -1) and N00 (floor 0)
    got = sources._tile_origins((-0.2, -0.2, 0.2, 0.2), 0, 0)
    assert sorted(got) == [(-1, -1), (-1, 0), (0, -1), (0, 0)]
    # entirely negative box -0.5..-0.4 -> floor = -1 -> S01 / W001
    assert sources._tile_origins((-0.5, -0.5, -0.4, -0.4), 0, 0) == [(-1, -1)]


def test_tile_origins_clamped_at_antimeridian_and_poles():
    got = sources._tile_origins((179.9, 89.5, 180.0, 90.0), 0.1, 0.1)
    assert got == [(89, 179)]
    got = sources._tile_origins((-180.0, -90.0, -179.9, -89.5), 0.1, 0.1)
    assert got == [(-90, -180)]


# ------------------------------------------------- Copernicus DEM (mocked)
def test_copernicus_dem_crs_resolution_and_values(osm, tiles, tmp_path):
    inp = fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")
    with rasterio.open(inp.dem) as ds:
        assert ds.crs.to_epsg() == 32633  # lon 12.3 is in UTM zone 33
        assert ds.res == (30.0, 30.0)  # 30 m
        assert ds.nodata == -9999.0
        data = ds.read(1)
        assert (data == -9999.0).sum() == 0  # fully covered, no edge nodata
        assert np.isfinite(data).all()
        # origin snapped to the 30 m grid
        assert ds.transform.c % 30.0 == 0 and ds.transform.f % 30.0 == 0
        # covers the box plus the 200 m margin: at least 0.695 km + 0.4 km
        # = 1.09 km wide (> 36 px) and 0.556 + 0.4 = 0.96 km high (> 31 px)
        assert ds.width >= 36 and ds.height >= 31
        # sample interior pixels, compare with the planar surface
        to_ll = Transformer.from_crs(32633, 4326, always_xy=True)
        for r, c in [(5, 5), (ds.height // 2, ds.width // 2), (ds.height - 6, 10)]:
            x, y = ds.xy(r, c)
            lon, lat = to_ll.transform(x, y)
            # z = 100 + 100*(lon-12) + 50*(lat-51); merge() snaps to the source
            # grid, so allow half a source pixel (0.001 deg): 0.1 + 0.05 m
            assert data[r, c] == pytest.approx(float(_elev(lon, lat)), abs=0.2)
    assert inp.provenance["crs"] == "EPSG:32633"


def test_copernicus_explicit_crs(osm, tiles, tmp_path):
    inp = fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c", crs=25833)
    with rasterio.open(inp.dem) as ds:
        assert ds.crs.to_epsg() == 25833
    assert inp.roads.crs.to_epsg() == 25833
    assert inp.buildings.crs.to_epsg() == 25833


def test_copernicus_opens_all_tiles_of_2x2_box(osm, monkeypatch, tmp_path):
    ft = FakeTiles(
        monkeypatch,
        tmp_path,
        [(51, 12), (51, 13), (52, 12), (52, 13)],
    )
    box = (12.9990, 51.9990, 13.0010, 52.0010)
    FakeOSM(monkeypatch, box)
    inp = fetch_inputs(box, cache_dir=tmp_path / "c")
    assert sorted(ft.opened) == sorted(
        sources._tile_url(la, lo) for la in (51, 52) for lo in (12, 13)
    )
    with rasterio.open(inp.dem) as ds:
        data = ds.read(1)
        assert (data == -9999.0).sum() == 0  # seams between tiles leave no gaps
        # continuity across the seam: surface is the same function on all tiles
        to_ll = Transformer.from_crs(ds.crs, 4326, always_xy=True)
        r, c = ds.height // 2, ds.width // 2
        lon, lat = to_ll.transform(*ds.xy(r, c))
        assert data[r, c] == pytest.approx(float(_elev(lon, lat)), abs=0.2)


def test_missing_tiles_are_skipped(osm, monkeypatch, tmp_path):
    # only the two tiles at lat 51 exist; the box touches lat 51/52 and lon 12/13
    box = (12.9990, 51.9990, 13.0010, 52.0010)
    ft = FakeTiles(monkeypatch, tmp_path, [(51, 12), (51, 13)])
    FakeOSM(monkeypatch, box)
    inp = fetch_inputs(box, cache_dir=tmp_path / "c")
    assert len(ft.opened) == 4  # all four tried, two answered 404
    assert Path(inp.dem).exists()


def test_all_tiles_missing_raises_value_error(osm, monkeypatch, tmp_path):
    FakeTiles(monkeypatch, tmp_path, [])
    cache = tmp_path / "c"
    with pytest.raises(FetchError, match="No Copernicus GLO-30 tile"):
        fetch_inputs(LEIPZIG, cache_dir=cache)
    # no half-written DEM is left behind as the final file
    assert not list(cache.glob("dem_copernicus_*.tif"))


def test_other_tile_io_errors_propagate(osm, monkeypatch, tmp_path):
    def boom(fp, *a, **k):
        raise RasterioIOError("invalid TIFF header")

    monkeypatch.setattr(rasterio, "open", boom)
    with pytest.raises(RasterioIOError, match="invalid TIFF"):
        fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")


# ---------------------------------------------------------- local DEM path
def _local_dem(path, crs, *, bbox=LEIPZIG, pad_m=300.0, res_m=10.0, shift_m=0.0):
    """Write a DEM that covers bbox (plus pad) in the given CRS."""
    to_crs = Transformer.from_crs(4326, crs, always_xy=True)
    xs, ys = zip(
        *[to_crs.transform(x, y) for x, y in [bbox[:2], bbox[2:]]], strict=True
    )
    geographic = rasterio.crs.CRS.from_user_input(crs).is_geographic
    if geographic:
        res = 1e-4
        pad = 0.003
    else:
        res = res_m
        pad = pad_m
    x0, x1 = min(xs) - pad + shift_m, max(xs) + pad + shift_m
    y0, y1 = min(ys) - pad, max(ys) + pad
    w = int((x1 - x0) / res)
    h = int((y1 - y0) / res)
    data = np.full((h, w), 123.0, dtype="float32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=h,
        width=w,
        count=1,
        dtype="float32",
        crs=crs,
        transform=from_origin(x0, y1, res, res),
        nodata=-9999.0,
    ) as ds:
        ds.write(data, 1)
    return path


def test_local_projected_dem_passes_through(osm, tmp_path):
    dem = _local_dem(tmp_path / "dgm.tif", "EPSG:25833")
    cache = tmp_path / "c"
    inp = fetch_inputs(LEIPZIG, cache_dir=cache, dem_source=dem)
    assert Path(inp.dem) == dem  # untouched, nothing copied
    assert not list(cache.glob("dem_file_*"))
    assert inp.roads.crs.to_epsg() == 25833  # keeps the DEM's own CRS
    assert inp.buildings.crs.to_epsg() == 25833
    assert inp.provenance["dem_source"] == "file"
    assert inp.provenance["dem_path"] == str(dem)
    assert inp.provenance["dem"]["source"] == "file"
    assert inp.provenance["crs"] == "EPSG:25833"


def test_local_projected_dem_str_path_and_uppercase_copernicus_word(osm, tmp_path):
    dem = _local_dem(tmp_path / "dgm.tif", "EPSG:25833")
    inp = fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c", dem_source=str(dem))
    assert Path(inp.dem) == dem


def test_local_geographic_dem_is_reprojected_into_cache(osm, tmp_path):
    dem = _local_dem(tmp_path / "geo.tif", "EPSG:4326")
    cache = tmp_path / "c"
    inp = fetch_inputs(LEIPZIG, cache_dir=cache, dem_source=dem)
    out = Path(inp.dem)
    assert out.parent == cache and out.name.startswith("dem_file_")
    with rasterio.open(out) as ds:
        assert ds.crs.to_epsg() == 32633  # UTM zone of the box
        assert ds.crs.is_projected
        valid = ds.read(1)[ds.read(1) != ds.nodata]
        assert valid.size > 0
        assert np.allclose(valid, 123.0)  # constant surface stays constant
    assert inp.roads.crs.to_epsg() == 32633
    # second call reuses the cached reprojection
    mtime = out.stat().st_mtime_ns
    inp2 = fetch_inputs(LEIPZIG, cache_dir=cache, dem_source=dem)
    assert Path(inp2.dem) == out and out.stat().st_mtime_ns == mtime


def test_local_projected_dem_reprojected_when_crs_differs(osm, tmp_path):
    dem = _local_dem(tmp_path / "dgm.tif", "EPSG:25833")
    inp = fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c", dem_source=dem, crs=32633)
    assert Path(inp.dem) != dem
    with rasterio.open(inp.dem) as ds:
        assert ds.crs.to_epsg() == 32633


def test_local_dem_not_overlapping_raises(osm, tmp_path):
    far = (13.30, 52.31, 13.31, 52.315)  # ~ 120 km away
    dem = _local_dem(tmp_path / "far.tif", "EPSG:25833", bbox=far)
    with pytest.raises(FetchError, match="does not overlap") as ei:
        fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c", dem_source=dem)
    assert isinstance(ei.value, ValueError)


def test_local_dem_missing_file_raises(osm, tmp_path):
    with pytest.raises(FetchError, match="does not exist"):
        fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c", dem_source=tmp_path / "x.tif")


def test_local_dem_without_crs_raises(osm, tmp_path):
    p = tmp_path / "nocrs.tif"
    with rasterio.open(
        p,
        "w",
        driver="GTiff",
        height=4,
        width=4,
        count=1,
        dtype="float32",
        transform=from_origin(0, 4, 1, 1),
    ) as ds:
        ds.write(np.ones((4, 4), "float32"), 1)
    with pytest.raises(FetchError, match="no CRS"):
        fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c", dem_source=p)


# ------------------------------------------------------------ roads/buildings
def test_roads_columns_dtypes_and_crs(osm, tiles, tmp_path):
    inp = fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")
    roads = inp.roads
    assert list(roads.columns) == ["osmid", "highway", "name", "geometry"]
    assert len(roads) == 2
    assert roads.crs.to_epsg() == 32633
    assert all(g.geom_type == "LineString" for g in roads.geometry)
    assert roads["osmid"].tolist() == ["11;12", "13"]  # lists are ;-joined
    assert roads["highway"].tolist() == ["residential;tertiary", "residential"]
    assert (roads["name"] == "").all()  # no name column in the OSM data
    assert roads.index.tolist() == [0, 1]  # (u, v, key) index flattened


def test_roads_name_kept_when_present(monkeypatch, tiles, tmp_path):
    fo = FakeOSM(monkeypatch)
    fo.edges["name"] = ["Hauptstrasse", "Nebenweg"]
    inp = fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")
    assert inp.roads["name"].tolist() == ["Hauptstrasse", "Nebenweg"]


def test_roads_name_nan_becomes_empty_and_list_joined(monkeypatch, tiles, tmp_path):
    fo = FakeOSM(monkeypatch)
    fo.edges["name"] = [float("nan"), ["A-Strasse", "B-Weg"]]
    inp = fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")
    assert inp.roads["name"].tolist() == ["", "A-Strasse;B-Weg"]


def test_highway_filter_default_and_custom_passed_through(osm, tiles, tmp_path):
    fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")
    assert osm.graph_args == (LEIPZIG,)
    assert osm.graph_kwargs["custom_filter"] == [
        '["highway"~"^(primary|secondary|tertiary|unclassified|residential|'
        'living_street|road|primary_link|secondary_link|tertiary_link)$"]',
        '["highway"="service"]["service"!~"^(parking_aisle|driveway)$"]',
    ]
    for h in ("service", "road", "primary_link", "secondary_link", "tertiary_link"):
        assert h in DEFAULT_HIGHWAY
    for h in ("motorway", "trunk", "track", "footway", "path", "parking_aisle"):
        assert h not in DEFAULT_HIGHWAY
    assert osm.graph_kwargs["retain_all"] is True
    fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c2", highway=("residential", "service"))
    assert osm.graph_kwargs["custom_filter"] == [
        '["highway"~"^(residential)$"]',
        '["highway"="service"]["service"!~"^(parking_aisle|driveway)$"]',
    ]
    fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c3", highway=("primary", "tertiary"))
    assert osm.graph_kwargs["custom_filter"] == ['["highway"~"^(primary|tertiary)$"]']


def test_buildings_polygons_only(osm, tiles, tmp_path):
    inp = fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")
    b = inp.buildings
    assert len(b) == 2  # the Point (node 103) is dropped
    assert all(g.geom_type == "Polygon" for g in b.geometry)
    assert list(b.columns) == ["osmid", "building", "geometry"]
    assert b["osmid"].tolist() == ["101", "102"]  # last index level, as str
    assert b["building"].tolist() == ["yes", "house"]
    assert b.crs.to_epsg() == 32633
    assert osm.features_args[0] == (LEIPZIG,)
    assert osm.features_args[1]["tags"] == {"building": True}


def test_building_footprint_area_survives_reprojection(osm, tiles, tmp_path):
    # 1e-4 deg square at 51.3125 N: 1e-4*111.2 km/deg... in metres:
    # width 1e-4 * 111_320 * cos(51.3125 deg)=0.62547 -> 6.963 m ; height 11.12 m
    # (ellipsoidal metres/deg: ~111.25 lat, ~69.6 lon) -> 6.96 * 11.125 = 77.5 m2
    inp = fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")
    area = inp.buildings.geometry.area.iloc[0]
    # UTM scale factor is ~0.9996 at the centre of the zone, 33 is 0.3 deg off it
    assert area == pytest.approx(77.5, rel=0.02)


def test_empty_roads_raise_value_error(monkeypatch, tiles, tmp_path):
    fo = FakeOSM(monkeypatch)
    fo.edges = fo.edges.iloc[0:0]
    with pytest.raises(FetchError, match="no roads"):
        fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")


def test_osmnx_insufficient_response_on_roads_is_value_error(
    monkeypatch, tiles, tmp_path
):
    import osmnx as ox
    from osmnx._errors import InsufficientResponseError

    FakeOSM(monkeypatch)

    def raise_empty(*a, **k):
        raise InsufficientResponseError("no data")

    monkeypatch.setattr(ox, "graph_from_bbox", raise_empty)
    with pytest.raises(FetchError, match="no roads"):
        fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")


def test_empty_buildings_raise_value_error(monkeypatch, tiles, tmp_path):
    fo = FakeOSM(monkeypatch)
    fo.feats = fo.feats.iloc[[2]]  # only the Point
    with pytest.raises(FetchError, match="no buildings"):
        fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")


def test_osmnx_insufficient_response_on_buildings_is_value_error(
    monkeypatch, tiles, tmp_path
):
    import osmnx as ox
    from osmnx._errors import InsufficientResponseError

    FakeOSM(monkeypatch)

    def raise_empty(*a, **k):
        raise InsufficientResponseError("no data")

    monkeypatch.setattr(ox, "features_from_bbox", raise_empty)
    with pytest.raises(FetchError, match="no buildings"):
        fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")


def test_unrelated_osm_errors_propagate(monkeypatch, tiles, tmp_path):
    import osmnx as ox

    FakeOSM(monkeypatch)

    def bad(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(ox, "graph_from_bbox", bad)
    with pytest.raises(RuntimeError, match="disk on fire"):
        fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")


# ------------------------------------------------------------------- cache
def test_second_call_hits_cache(osm, tiles, tmp_path):
    cache = tmp_path / "c"
    first = fetch_inputs(LEIPZIG, cache_dir=cache)
    assert osm.calls == {"graph": 1, "gdfs": 1, "features": 1}
    n_opened = len(tiles.opened)
    second = fetch_inputs(LEIPZIG, cache_dir=cache)
    assert osm.calls == {"graph": 1, "gdfs": 1, "features": 1}  # unchanged
    assert len(tiles.opened) == n_opened  # no new tile requests
    assert Path(second.dem) == Path(first.dem)
    assert len(second.roads) == len(first.roads) == 2
    assert len(second.buildings) == len(first.buildings) == 2
    assert second.roads["osmid"].tolist() == first.roads["osmid"].tolist()


def test_cache_key_rounds_bbox_to_five_decimals(osm, tiles, tmp_path):
    cache = tmp_path / "c"
    fetch_inputs(LEIPZIG, cache_dir=cache)
    w, s, e, n = LEIPZIG
    fetch_inputs((w + 1e-7, s, e, n - 1e-7), cache_dir=cache)
    assert osm.calls["graph"] == 1  # same rounded box -> cache hit
    fetch_inputs((w + 1e-3, s, e, n), cache_dir=cache)
    assert osm.calls["graph"] == 2  # different box -> miss


def test_cache_key_depends_on_highway_filter(osm, tiles, tmp_path):
    cache = tmp_path / "c"
    fetch_inputs(LEIPZIG, cache_dir=cache)
    fetch_inputs(LEIPZIG, cache_dir=cache, highway=("residential",))
    assert osm.calls["graph"] == 2
    # same set in another order is the same filter
    fetch_inputs(LEIPZIG, cache_dir=cache, highway=("residential",))
    assert osm.calls["graph"] == 2
    # buildings do not depend on the highway filter
    assert osm.calls["features"] == 1


def test_offline_empty_cache_raises_and_creates_nothing(osm, tmp_path):
    cache = tmp_path / "never"
    with pytest.raises(OfflineCacheMissError, match="offline=True") as ei:
        fetch_inputs(LEIPZIG, cache_dir=cache, offline=True)
    assert isinstance(ei.value, FileNotFoundError)
    assert isinstance(ei.value, FetchError) and isinstance(ei.value, ValueError)
    assert not cache.exists()
    assert osm.total == 0


def test_offline_uses_populated_cache(osm, tiles, tmp_path):
    cache = tmp_path / "c"
    fetch_inputs(LEIPZIG, cache_dir=cache)
    osm_calls = dict(osm.calls)
    inp = fetch_inputs(LEIPZIG, cache_dir=cache, offline=True)
    assert osm.calls == osm_calls
    assert len(inp.roads) == 2


def test_offline_missing_dem_raises(osm, tiles, tmp_path):
    cache = tmp_path / "c"
    fetch_inputs(LEIPZIG, cache_dir=cache)
    for f in cache.glob("dem_copernicus_*.tif"):
        f.unlink()
    with pytest.raises(OfflineCacheMissError, match="cached DEM"):
        fetch_inputs(LEIPZIG, cache_dir=cache, offline=True)


def test_default_cache_dir_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert sources._default_cache_dir() == tmp_path / "pysewer" / "sources"


# -------------------------------------------------- provenance and progress
def test_provenance_content_and_json_serialisable(osm, tiles, tmp_path):
    inp = fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")
    p = inp.provenance
    json.dumps(p)  # must not raise
    assert p["bbox_wgs84"] == list(LEIPZIG)
    assert p["crs"] == "EPSG:32633"
    assert p["dem_source"] == "copernicus"
    assert p["dem"]["attribution"] == COPERNICUS_DERIVED_ATTRIBUTION
    assert COPERNICUS_DERIVED_ATTRIBUTION.startswith("produced using")
    assert "©" in COPERNICUS_ATTRIBUTION and "©" in OSM_ATTRIBUTION
    assert p["dem"]["cache_hit"] is False
    assert p["roads"]["cache_hit"] is False and p["buildings"]["cache_hit"] is False
    assert "Copernicus" in p["dem"]["source"]
    assert p["dem"]["url"] == COPERNICUS_URL_TEMPLATE
    assert p["roads"]["attribution"] == OSM_ATTRIBUTION
    assert p["buildings"]["attribution"] == OSM_ATTRIBUTION
    assert "OpenStreetMap" in OSM_ATTRIBUTION and "ODbL" in OSM_ATTRIBUTION
    assert p["osm"]["highway"] == list(DEFAULT_HIGHWAY)
    assert p["fetched_at"].endswith("+00:00")  # UTC ISO timestamp
    from datetime import datetime

    datetime.fromisoformat(p["fetched_at"])


def test_provenance_of_local_dem_json_serialisable(osm, tmp_path):
    dem = _local_dem(tmp_path / "dgm.tif", "EPSG:25833")
    inp = fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c", dem_source=dem)
    json.dumps(inp.provenance)
    assert "attribution" not in inp.provenance["dem"]


def test_progress_stage_order(osm, tiles, tmp_path):
    seen = []
    fetch_inputs(
        LEIPZIG,
        cache_dir=tmp_path / "c",
        progress=lambda stage, frac, msg: seen.append((stage, frac, msg)),
    )
    stages = [s for s, _, _ in seen]
    assert stages == [
        "fetch:roads",
        "fetch:roads",
        "fetch:buildings",
        "fetch:buildings",
        "fetch:dem",
        "fetch:done",
    ]
    fracs = [f for _, f, _ in seen]
    assert fracs == sorted(fracs)
    assert fracs[0] == 0.0 and fracs[-1] == 1.0
    assert all(isinstance(m, str) and m for _, _, m in seen)
    assert "2 road segments" in seen[1][2]


# --------------------------------------------------------------- Overpass retry
def _status_error():
    from osmnx._errors import ResponseStatusCodeError

    return ResponseStatusCodeError("429 too many requests")


def test_with_retry_succeeds_after_two_failures(no_sleep):
    n = {"i": 0}

    def call():
        n["i"] += 1
        if n["i"] <= 2:
            raise _status_error()
        return "ok"

    assert sources._with_retry(call, 3, 5.0) == "ok"
    assert n["i"] == 3
    # exponential backoff 5 s, 10 s
    assert no_sleep == [5.0, 10.0]


def test_with_retry_gives_up_after_retries_plus_one_attempts(no_sleep):
    n = {"i": 0}

    def call():
        n["i"] += 1
        raise _status_error()

    with pytest.raises(Exception, match="429"):
        sources._with_retry(call, 3, 5.0)
    assert n["i"] == 4  # first try + 3 retries
    assert len(no_sleep) == 3
    assert no_sleep[:2] == [5.0, 10.0]
    assert sum(no_sleep) <= 3 * 5.0 + 1e-9  # total wait capped at retries*wait


def test_with_retry_zero_retries_calls_once(no_sleep):
    n = {"i": 0}

    def call():
        n["i"] += 1
        raise _status_error()

    with pytest.raises(Exception, match="429"):
        sources._with_retry(call, 0, 5.0)
    assert n["i"] == 1 and no_sleep == []


@pytest.mark.parametrize("name", ["ConnectionError", "Timeout"])
def test_with_retry_retries_requests_errors(no_sleep, name):
    import requests

    exc_cls = getattr(requests.exceptions, name)
    n = {"i": 0}

    def call():
        n["i"] += 1
        if n["i"] == 1:
            raise exc_cls("flaky")
        return "ok"

    assert sources._with_retry(call, 3, 5.0) == "ok"
    assert n["i"] == 2 and no_sleep == [5.0]


def test_with_retry_other_errors_not_retried(no_sleep):
    n = {"i": 0}

    def call():
        n["i"] += 1
        raise KeyError("x")

    with pytest.raises(KeyError):
        sources._with_retry(call, 3, 5.0)
    assert n["i"] == 1 and no_sleep == []


def test_fetch_retries_overpass_through_osmnx(monkeypatch, tiles, tmp_path, no_sleep):
    import osmnx as ox

    fo = FakeOSM(monkeypatch)
    ok = ox.graph_from_bbox
    n = {"i": 0}

    def flaky(*a, **k):
        n["i"] += 1
        if n["i"] <= 2:
            raise _status_error()
        return ok(*a, **k)

    monkeypatch.setattr(ox, "graph_from_bbox", flaky)
    inp = fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")
    assert n["i"] == 3 and fo.calls["graph"] == 1
    assert no_sleep == [5.0, 10.0]
    assert len(inp.roads) == 2


def test_fetch_honours_retries_and_retry_wait(monkeypatch, tiles, tmp_path, no_sleep):
    import osmnx as ox

    FakeOSM(monkeypatch)
    n = {"i": 0}

    def always(*a, **k):
        n["i"] += 1
        raise _status_error()

    monkeypatch.setattr(ox, "graph_from_bbox", always)
    with pytest.raises(Exception, match="429"):
        fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c", retries=1, retry_wait=2.0)
    assert n["i"] == 2 and no_sleep == [2.0]


def test_fetch_gives_up_after_four_overpass_errors(
    monkeypatch, tiles, tmp_path, no_sleep
):
    import osmnx as ox

    FakeOSM(monkeypatch)
    n = {"i": 0}

    def always(*a, **k):
        n["i"] += 1
        raise _status_error()

    monkeypatch.setattr(ox, "features_from_bbox", always)
    with pytest.raises(Exception, match="429"):
        fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")
    assert n["i"] == 4


# --------------------------------------------------------------- light import
def test_import_sources_does_not_load_heavy_modules():
    code = (
        "import sys, pysewer\n"
        "before = set(sys.modules)\n"
        "import pysewer.sources\n"
        "bad = [m for m in ('osmnx', 'matplotlib') if m in sys.modules]\n"
        "assert not bad, bad\n"
        "new = {m.split('.')[0] for m in set(sys.modules) - before}\n"
        "assert not new & {'osmnx', 'rasterio', 'matplotlib'}, new\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(REPO)},
    )
    assert r.returncode == 0, r.stderr


# ------------------------------------------------------------------- live test
@pytest.mark.network
def test_live_leipzig_generate(tmp_path):
    from pysewer.pipeline import generate

    inp = fetch_inputs(LEIPZIG, cache_dir=tmp_path / "live")
    bundle = generate(inp, sink="lowest")
    assert len(bundle.pipes) > 0


# ----------------------------------------------- revised-behaviour additions
def test_unknown_dem_source_raises_value_error(osm, tmp_path):
    cache = tmp_path / "c"
    with pytest.raises(ValueError, match="unknown dem_source"):
        fetch_inputs(LEIPZIG, cache_dir=cache, dem_source="nasadem")
    with pytest.raises(ValueError, match="unknown dem_source"):
        fetch_inputs(LEIPZIG, cache_dir=cache, dem_source=str(tmp_path / "nofile.tif"))
    assert osm.total == 0
    assert "copernicus" in DEM_SOURCES and callable(DEM_SOURCES["copernicus"])


def test_registered_dem_source_is_called(osm, monkeypatch, tmp_path):
    calls = []

    def fake(bbox, crs, out):
        calls.append((bbox, crs.to_epsg()))
        _local_dem(out, "EPSG:32633")

    monkeypatch.setitem(DEM_SOURCES, "copernicus", fake)
    inp = fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")
    assert calls == [(LEIPZIG, 32633)]
    assert Path(inp.dem).exists()
    fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")
    assert len(calls) == 1  # second call is a cache hit


def test_osmnx_cache_folder_set_and_restored(monkeypatch, tiles, tmp_path):
    import osmnx as ox

    fo = FakeOSM(monkeypatch)
    ox.settings.cache_folder = "/some/previous"
    ox.settings.use_cache = False
    cache = tmp_path / "c"
    try:
        fetch_inputs(LEIPZIG, cache_dir=cache)
        assert fo.cache_folder_seen == str(cache / "osmnx")
        assert ox.settings.cache_folder == "/some/previous"
        assert ox.settings.use_cache is False
    finally:
        ox.settings.cache_folder = "./cache"
        ox.settings.use_cache = True


def test_osmnx_settings_restored_on_error(monkeypatch, tiles, tmp_path):
    import osmnx as ox

    FakeOSM(monkeypatch)

    def bad(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(ox, "graph_from_bbox", bad)
    before = ox.settings.cache_folder
    with pytest.raises(RuntimeError):
        fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")
    assert ox.settings.cache_folder == before


def test_no_partial_files_left_after_success(osm, tiles, tmp_path):
    cache = tmp_path / "c"
    fetch_inputs(LEIPZIG, cache_dir=cache)
    assert not [p for p in cache.iterdir() if ".part" in p.name]
    assert len(list(cache.glob("roads_*.gpkg"))) == 1
    assert len(list(cache.glob("buildings_*.gpkg"))) == 1
    assert len(list(cache.glob("dem_copernicus_*.tif"))) == 1


def test_failed_dem_leaves_no_partial_file(osm, monkeypatch, tmp_path):
    def broken(bbox, crs, out):
        Path(out).write_bytes(b"half")
        raise RuntimeError("download died")

    monkeypatch.setitem(DEM_SOURCES, "copernicus", broken)
    cache = tmp_path / "c"
    with pytest.raises(RuntimeError, match="download died"):
        fetch_inputs(LEIPZIG, cache_dir=cache)
    assert not list(cache.glob("dem_*"))
    assert not [p for p in cache.iterdir() if ".part" in p.name]


def test_cache_hit_flags_and_fetched_at_from_mtime(osm, tiles, tmp_path):
    import os

    cache = tmp_path / "c"
    fetch_inputs(LEIPZIG, cache_dir=cache)
    # 1600000000 = 2020-09-13T12:26:40 UTC (oldest, roads); others are later
    os.utime(next(cache.glob("roads_*.gpkg")), (1_600_000_000,) * 2)
    os.utime(next(cache.glob("buildings_*.gpkg")), (1_600_000_100,) * 2)
    os.utime(next(cache.glob("dem_copernicus_*.tif")), (1_600_000_200,) * 2)
    inp = fetch_inputs(LEIPZIG, cache_dir=cache)
    p = inp.provenance
    assert p["fetched_at"] == "2020-09-13T12:26:40+00:00"
    assert p["roads"]["cache_hit"] is True
    assert p["buildings"]["cache_hit"] is True
    assert p["dem"]["cache_hit"] is True


def test_partial_cache_hit_flags(osm, tiles, tmp_path):
    cache = tmp_path / "c"
    fetch_inputs(LEIPZIG, cache_dir=cache)
    next(cache.glob("buildings_*.gpkg")).unlink()
    p = fetch_inputs(LEIPZIG, cache_dir=cache).provenance
    assert p["roads"]["cache_hit"] is True
    assert p["buildings"]["cache_hit"] is False
    assert p["dem"]["cache_hit"] is True


def test_local_dem_is_cropped_to_box_plus_margin(osm, tmp_path):
    # 10 km padding on each side at 10 m: ~ 2000 x 2000 px, far more than 4x
    # the window (box + 2 x 200 m margin each side, ~ 1.1 km x 1.0 km... ~110 px)
    dem = _local_dem(tmp_path / "big.tif", "EPSG:25833", pad_m=5000.0)
    cache = tmp_path / "c"
    inp = fetch_inputs(LEIPZIG, cache_dir=cache, dem_source=dem)
    out = Path(inp.dem)
    assert out != dem and out.parent == cache
    with rasterio.open(dem) as src, rasterio.open(out) as ds:
        assert ds.crs.to_epsg() == 25833
        assert ds.res == src.res
        assert ds.width * ds.height < src.width * src.height / 4
        # box (0.70 x 0.56 km) + 2 x 400 m margin => about 1.5 km x 1.4 km,
        # nowhere near the 11 km source
        assert ds.width * 10.0 < 2500 and ds.height * 10.0 < 2500
        assert ds.width * 10.0 > 700 and ds.height * 10.0 > 560
        assert np.allclose(ds.read(1), 123.0)
    assert inp.provenance["dem"]["cache_hit"] is True


# --------------------------------------------------- preflight_building_count
class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")

    def json(self):
        return self._p


def test_preflight_building_count_parses_total_and_builds_query(monkeypatch):
    import requests

    seen = {}

    def post(url, data=None, timeout=None, **kw):
        seen.update(url=url, data=data, timeout=timeout, **kw)
        return _Resp({"elements": [{"type": "count", "tags": {"total": "1234"}}]})

    monkeypatch.setattr(requests, "post", post)
    assert sources.preflight_building_count(LEIPZIG) == 1234
    assert seen["url"].endswith("/interpreter")
    q = seen["data"]["data"]
    # Overpass boxes are (south, west, north, east)
    assert "(51.31,12.3,51.315,12.31)" in q
    assert 'way["building"]' in q and 'relation["building"]' in q
    assert "out count" in q
    assert seen["timeout"] == 60.0


def test_preflight_building_count_empty_elements_is_zero(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp({"elements": []}))
    assert sources.preflight_building_count(LEIPZIG) == 0


def test_preflight_building_count_retries_connection_errors(monkeypatch, no_sleep):
    import requests

    n = {"i": 0}

    def post(*a, **k):
        n["i"] += 1
        if n["i"] < 3:
            raise requests.exceptions.ConnectionError("down")
        return _Resp({"elements": [{"tags": {"total": "7"}}]})

    monkeypatch.setattr(requests, "post", post)
    assert sources.preflight_building_count(LEIPZIG) == 7
    assert n["i"] == 3 and no_sleep == [5.0, 10.0]


def test_preflight_building_count_gives_up(monkeypatch, no_sleep):
    import requests

    n = {"i": 0}

    def post(*a, **k):
        n["i"] += 1
        raise requests.exceptions.Timeout("slow")

    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(requests.exceptions.Timeout):
        sources.preflight_building_count(LEIPZIG, retries=2, retry_wait=1.0)
    assert n["i"] == 3


def test_preflight_building_count_validates_bbox():
    with pytest.raises(ValueError):
        sources.preflight_building_count((12.31, 51.31, 12.30, 51.315))


# ------------------------------------------------ final-round behaviour
def test_http_headers_fall_back_when_osmnx_helper_missing(monkeypatch):
    import osmnx._http as http

    monkeypatch.delattr(http, "_get_http_headers")
    headers = sources._http_headers()
    assert headers["User-Agent"].startswith("pysewer/")
    assert headers["Accept"] == "*/*"
    assert headers["Referer"] == sources._REPO_URL


def test_http_headers_use_osmnx_helper_with_pysewer_agent():
    headers = sources._http_headers()
    assert headers["User-Agent"].startswith("pysewer/")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("'h' responded: 502 Bad Gateway", True),
        ("'h' responded: 429 Too Many Requests", True),
        ("'h' responded: 406 Not Acceptable", False),
        ("'h' responded: 400 Bad Request", False),
    ],
)
def test_is_transient_osmnx_status_errors(message, expected):
    from osmnx._errors import ResponseStatusCodeError

    assert sources._is_transient(ResponseStatusCodeError(message)) is expected


def test_is_transient_requests_http_status():
    import requests

    def err(code):
        resp = requests.Response()
        resp.status_code = code
        return requests.exceptions.HTTPError(response=resp)

    assert sources._is_transient(err(503))
    assert sources._is_transient(err(429))
    assert not sources._is_transient(err(404))
    assert sources._is_transient(requests.exceptions.ConnectionError())
    assert sources._is_transient(requests.exceptions.Timeout())
    assert not sources._is_transient(ValueError("x"))


@pytest.mark.parametrize(
    "text", ["CURL error: Could not resolve host: x", "Operation timed out"]
)
def test_copernicus_network_failure_becomes_connection_error(
    osm, monkeypatch, tmp_path, text
):
    def boom(fp, *a, **k):
        raise RasterioIOError(text)

    monkeypatch.setattr(rasterio, "open", boom)
    with pytest.raises(ConnectionError, match="Copernicus"):
        fetch_inputs(LEIPZIG, cache_dir=tmp_path / "c")


def test_cache_path_helpers_are_shared(osm, tiles, tmp_path):
    cache = tmp_path / "c"
    fetch_inputs(LEIPZIG, cache_dir=cache)
    assert sources._roads_cache_path(cache, LEIPZIG, sources.DEFAULT_HIGHWAY).exists()
    assert sources._buildings_cache_path(cache, LEIPZIG).exists()


def test_offline_file_dem_passthrough_does_not_touch_cache(osm, tmp_path):
    cache = tmp_path / "c"
    fetch_inputs(
        LEIPZIG,
        cache_dir=cache,
        dem_source=_local_dem(tmp_path / "dgm.tif", "EPSG:25833"),
    )
    before = sorted(p.name for p in cache.iterdir())
    again = fetch_inputs(
        LEIPZIG,
        cache_dir=cache,
        offline=True,
        dem_source=tmp_path / "dgm.tif",
    )
    assert Path(again.dem) == tmp_path / "dgm.tif"
    assert sorted(p.name for p in cache.iterdir()) == before
