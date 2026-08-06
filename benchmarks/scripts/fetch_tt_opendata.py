# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""Build pysewer benchmark cases for Trinidad & Tobago from open data.

Per settlement: OSM roads (drive network) + OSM building footprints
(centroids) via osmnx, and terrain from the Copernicus GLO-30 DEM (public
AWS bucket), merged and reprojected to EPSG:32620 (UTM 20N). Sink = lowest
road vertex (no open WWTP registry). Data quality caveat: OSM building
coverage in T&T is partial — building counts are reported so thin towns can
be judged accordingly.

Outputs per town: work/benchmarks/tt/<slug>/{roads.gpkg,buildings.gpkg,dem.tif}
and a generated case YAML under work/benchmarks/cases_generated/.

    python benchmarks/scripts/fetch_tt_opendata.py [--towns "..."] [--max-buildings N]
"""

import argparse
import json
import re
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TT_DIR = REPO_ROOT / "work" / "benchmarks" / "tt"
CASE_DIR = REPO_ROOT / "work" / "benchmarks" / "cases_generated"
UTM = "EPSG:32620"

# Copernicus GLO-30 tiles covering Trinidad & Tobago
DEM_TILES = [
    "Copernicus_DSM_COG_10_N10_00_W062_00_DEM",
    "Copernicus_DSM_COG_10_N10_00_W061_00_DEM",
    "Copernicus_DSM_COG_10_N11_00_W061_00_DEM",
]
DEM_URL = "https://copernicus-dem-30m.s3.amazonaws.com/{t}/{t}.tif"

DEFAULT_TOWNS = [
    "Scarborough, Tobago, Trinidad and Tobago",
    "Roxborough, Trinidad and Tobago",
    "Point Fortin, Trinidad and Tobago",
    "Siparia, Trinidad and Tobago",
    "Penal, Trinidad and Tobago",
    "Princes Town, Trinidad and Tobago",
    "Rio Claro, Trinidad and Tobago",
    "Couva, Trinidad and Tobago",
    "Sangre Grande, Trinidad and Tobago",
    "Arima, Trinidad and Tobago",
    "Chaguanas, Trinidad and Tobago",
    "San Fernando, Trinidad and Tobago",
    "Port of Spain, Trinidad and Tobago",
]


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "_", name.split(",")[0].lower()).strip("_")


def fetch_dem_mosaic():
    """Download + merge the GLO-30 tiles once, reprojected to UTM 20N."""
    import rasterio
    from rasterio.merge import merge
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    mosaic_path = TT_DIR / "dem_tt_utm.tif"
    if mosaic_path.exists():
        return mosaic_path
    TT_DIR.mkdir(parents=True, exist_ok=True)
    tile_paths = []
    for tile in DEM_TILES:
        tp = TT_DIR / f"{tile}.tif"
        if not tp.exists():
            print(f"  downloading {tile} ...")
            urllib.request.urlretrieve(DEM_URL.format(t=tile), tp)
        tile_paths.append(tp)

    sources = [rasterio.open(p) for p in tile_paths]
    mosaic, transform = merge(sources)
    meta = sources[0].meta.copy()
    meta.update(
        height=mosaic.shape[1], width=mosaic.shape[2], transform=transform, count=1
    )
    tmp = TT_DIR / "dem_tt_4326.tif"
    with rasterio.open(tmp, "w", **meta) as dst:
        dst.write(mosaic[0], 1)
    for s in sources:
        s.close()

    with rasterio.open(tmp) as src:
        dst_transform, w, h = calculate_default_transform(
            src.crs, UTM, src.width, src.height, *src.bounds
        )
        meta = src.meta.copy()
        meta.update(crs=UTM, transform=dst_transform, width=w, height=h)
        with rasterio.open(mosaic_path, "w", **meta) as dst:
            reproject(
                source=rasterio.band(src, 1),
                destination=rasterio.band(dst, 1),
                dst_transform=dst_transform,
                dst_crs=UTM,
                resampling=Resampling.bilinear,
            )
    tmp.unlink()
    print(f"  DEM mosaic ready: {mosaic_path}")
    return mosaic_path


def clip_dem(mosaic_path, polygon_utm, out_path):
    import rasterio
    from rasterio.mask import mask

    with rasterio.open(mosaic_path) as src:
        clipped, transform = mask(src, [polygon_utm], crop=True)
        meta = src.meta.copy()
        meta.update(
            height=clipped.shape[1], width=clipped.shape[2], transform=transform
        )
        with rasterio.open(out_path, "w", **meta) as dst:
            dst.write(clipped)


def fetch_town(town, mosaic_path, max_buildings):
    import geopandas as gpd
    import osmnx as ox

    slug = slugify(town)
    out = TT_DIR / slug
    case_path = CASE_DIR / f"tt_{slug}.yaml"
    if case_path.exists():
        print(f"{slug}: already fetched")
        return case_path
    out.mkdir(parents=True, exist_ok=True)

    print(f"{slug}: geocoding + OSM download ...")
    boundary = ox.geocode_to_gdf(town)
    polygon = boundary.geometry.iloc[0]

    graph = ox.graph_from_polygon(polygon, network_type="drive", retain_all=True)
    roads = ox.graph_to_gdfs(graph, nodes=False)[["geometry"]].reset_index(drop=True)
    buildings = ox.features_from_polygon(polygon, tags={"building": True})
    buildings = buildings[buildings.geometry.notna()].copy()
    buildings["geometry"] = buildings.geometry.centroid
    buildings = buildings[["geometry"]].reset_index(drop=True)

    n_buildings = len(buildings)
    if n_buildings < 20:
        print(f"{slug}: only {n_buildings} OSM buildings — skipped (thin coverage)")
        return None
    if n_buildings > max_buildings:
        print(f"{slug}: {n_buildings} buildings > cap {max_buildings} — skipped for now")
        return None

    roads.to_crs(UTM).to_file(out / "roads.gpkg", driver="GPKG")
    buildings.to_crs(UTM).to_file(out / "buildings.gpkg", driver="GPKG")
    poly_utm = boundary.to_crs(UTM).geometry.iloc[0].buffer(300)
    clip_dem(mosaic_path, poly_utm, out / "dem.tif")

    CASE_DIR.mkdir(parents=True, exist_ok=True)
    rel = out.relative_to(REPO_ROOT)
    case_path.write_text(
        f"""name: tt_{slug}
description: >
  {town} — OSM roads/buildings (centroids) + Copernicus GLO-30 DEM,
  EPSG:32620. Open-data benchmark; sink = lowest road vertex.
  {n_buildings} buildings, {len(roads)} road segments.
dem: {rel}/dem.tif
roads: {rel}/roads.gpkg
buildings: {rel}/buildings.gpkg
sink: lowest
"""
    )
    print(f"{slug}: staged ({n_buildings} buildings, {len(roads)} roads)")
    return case_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--towns", nargs="*", default=DEFAULT_TOWNS)
    parser.add_argument("--max-buildings", type=int, default=15000)
    args = parser.parse_args()

    mosaic = fetch_dem_mosaic()
    manifest = {}
    for town in args.towns:
        try:
            case = fetch_town(town, mosaic, args.max_buildings)
            manifest[town] = str(case) if case else "skipped"
        except Exception as exc:  # noqa: BLE001 — continue with other towns
            print(f"{town}: FAILED — {type(exc).__name__}: {exc}")
            manifest[town] = f"error: {exc}"
    (TT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
