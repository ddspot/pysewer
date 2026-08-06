# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""Stage benchmark datasets into work/benchmarks/ (scratch, never committed).

- reviewer_reduced: clip data/from_reviewer roads/buildings (EPSG:32631)
  to the cropped_dem extent
- wasweta: copy the Wasweta II share (UTM 36S) off the Nextcloud sync dir
  (never compute against a live sync mount)

Idempotent: existing staged files are kept.
"""

import shutil
from pathlib import Path

import geopandas as gpd
import rasterio
from shapely.geometry import box

REPO_ROOT = Path(__file__).resolve().parents[2]
WORK = REPO_ROOT / "work" / "benchmarks"
WASWETA_SRC = Path("/Users/despot/Nextcloud/Shared with me/Wasweta_II_Test_Case")


def stage_reviewer_reduced():
    out = WORK / "reviewer"
    out.mkdir(parents=True, exist_ok=True)
    roads_out = out / "roads_reduced.gpkg"
    buildings_out = out / "buildings_reduced.gpkg"
    if roads_out.exists() and buildings_out.exists():
        print("reviewer_reduced: already staged")
        return
    with rasterio.open(REPO_ROOT / "data/from_reviewer/cropped_dem.tif") as src:
        extent = box(*src.bounds)
    roads = gpd.read_file(REPO_ROOT / "data/from_reviewer/roads.geojson")
    buildings = gpd.read_file(REPO_ROOT / "data/from_reviewer/buildings.geojson")
    gpd.clip(roads, extent).to_file(roads_out, driver="GPKG")
    gpd.clip(buildings, extent).to_file(buildings_out, driver="GPKG")
    print(
        f"reviewer_reduced: staged {len(gpd.read_file(roads_out))} roads, "
        f"{len(gpd.read_file(buildings_out))} buildings"
    )


def stage_wasweta():
    out = WORK / "wasweta"
    out.mkdir(parents=True, exist_ok=True)
    if (out / "dem_utm.tif").exists():
        print("wasweta: already staged")
        return
    if not WASWETA_SRC.exists():
        print(f"wasweta: source not found ({WASWETA_SRC}) — skipped")
        return
    for pattern in ("dem_utm.tif", "roads_utm.*", "buildings_utm.*"):
        for f in WASWETA_SRC.glob(pattern):
            shutil.copy2(f, out / f.name)
    print("wasweta: staged")


if __name__ == "__main__":
    stage_reviewer_reduced()
    stage_wasweta()
