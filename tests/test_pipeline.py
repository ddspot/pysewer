# SPDX-License-Identifier: GPL-3.0-only
"""``pysewer.pipeline.generate``: inputs -> NetworkBundle."""

import dataclasses
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import pytest
from pyproj import Transformer

import pysewer.pipeline as pipeline
from pysewer.config import manager
from pysewer.pipeline import Inputs, generate

DATA = Path(__file__).parent / "test_data"
DEM = DATA / "dem.tif"
ROADS = DATA / "roads_clipped.shp"
BUILDINGS = DATA / "buildings_clipped.shp"
SINK = (691350, 2553250)


@pytest.fixture(autouse=True)
def _clean_config():
    manager.reset_config()
    yield
    manager.reset_config()


@pytest.fixture(scope="module")
def inputs():
    return Inputs(DEM, ROADS, BUILDINGS)


@pytest.fixture(scope="module")
def default_run(inputs):
    manager.reset_config()
    calls = []
    b = generate(
        inputs, sink=SINK, name="demo", progress=lambda s, f, m: calls.append((s, f, m))
    )
    manager.reset_config()
    return b, calls


@pytest.fixture(scope="module")
def tmax3_run(inputs):
    manager.reset_config()
    b = generate(inputs, sink=SINK, overrides={"optimization": {"tmax": 3}})
    manager.reset_config()
    return b


def _stations(b):
    return int(b.nodes["role"].isin(["pumping_station", "lifting_station"]).sum())


def test_inputs_frozen():
    i = Inputs("a", "b", "c")
    assert i.provenance == {}
    with pytest.raises(dataclasses.FrozenInstanceError):
        i.dem = "x"


def test_default_run_counts(default_run):
    b, _ = default_run
    # a routed tree: nodes = pipes + 1
    assert len(b.nodes) == len(b.pipes) + 1
    assert len(b.pipes) > 0
    assert (b.nodes["role"] == "sink").sum() == 1
    assert (
        b.metadata["sinks"] == [b.nodes.index[b.nodes["role"] == "sink"][0]]
        or len(b.metadata["sinks"]) == 1
    )
    assert (b.nodes["role"] == "building").sum() > 0
    assert b.metadata["counts"] == {"pipes": len(b.pipes), "nodes": len(b.nodes)}
    assert b.metadata["name"] == "demo"
    assert b.name == "demo"


def test_default_provenance_file_layers(default_run):
    inp = default_run[0].metadata["provenance"]["inputs"]
    for key, path in (("dem", DEM), ("roads", ROADS), ("buildings", BUILDINGS)):
        assert inp[key]["source"] == "file"
        assert inp[key]["path"] == str(path)


def test_progress_stages_and_monotone(default_run):
    _, calls = default_run
    stages = [s for s, _, _ in calls]
    order = []
    for s in stages:
        if not order or order[-1] != s:
            order.append(s)
    assert order == ["load", "connect", "route_design", "bundle"]
    fracs = [f for _, f, _ in calls]
    assert fracs[0] == 0.0
    assert fracs[-1] == 1.0
    assert fracs == sorted(fracs)
    assert all(0.0 <= f <= 1.0 for f in fracs)
    assert all(isinstance(m, str) for _, _, m in calls)


def test_default_tmax_in_metadata(default_run):
    assert default_run[0].metadata["config"]["optimization"]["tmax"] == 8.0


def test_overrides_tmax(default_run, tmax3_run):
    assert tmax3_run.metadata["config"]["optimization"]["tmax"] == 3
    assert _stations(tmax3_run) > _stations(default_run[0])
    assert manager.get_config().optimization.tmax == 8.0


def test_overrides_restored_after_run(tmax3_run):
    # fixture ran with overrides; global config must be back to default
    assert manager.get_config().optimization.tmax == 8.0


def test_custom_config_preserved_and_merged(inputs):
    manager.set_config(custom_settings_dict={"optimization": {"tmin": 1.7}})
    custom = manager.get_config()
    b = generate(inputs, sink=SINK, overrides={"optimization": {"tmax": 3}})
    opt = b.metadata["config"]["optimization"]
    assert opt["tmin"] == 1.7
    assert opt["tmax"] == 3
    assert manager.get_config() is custom
    assert manager.get_config().optimization.tmin == 1.7
    assert manager.get_config().optimization.tmax == 8.0


def test_custom_config_used_without_overrides(inputs):
    manager.set_config(custom_settings_dict={"optimization": {"tmin": 1.7}})
    b = generate(inputs, sink=SINK)
    assert b.metadata["config"]["optimization"]["tmin"] == 1.7
    assert manager.get_config().optimization.tmin == 1.7


def test_config_restored_on_mid_run_error(inputs, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(pipeline, "route_and_design", boom)
    before = manager.get_config()
    with pytest.raises(RuntimeError, match="forced failure"):
        generate(inputs, sink=SINK, overrides={"optimization": {"tmax": 3}})
    assert manager.get_config() is before
    assert manager.get_config().optimization.tmax == 8.0


def test_sink_lowest(inputs):
    b = generate(inputs, sink="lowest")
    assert b.metadata["provenance"]["extra"]["run"]["sink"] == "lowest"
    assert (b.nodes["role"] == "sink").sum() == 1
    assert len(b.pipes) > 0


def test_sink_geographic_equals_projected(inputs, default_run):
    import rasterio

    with rasterio.open(DEM) as src:
        crs = src.crs
    lon, lat = Transformer.from_crs(crs, 4326, always_xy=True).transform(*SINK)
    b = generate(inputs, sink=(lon, lat), sink_crs=4326)
    assert b.pipes["length"].sum() == pytest.approx(
        default_run[0].pipes["length"].sum(), rel=1e-6
    )


def test_roads_geodataframe_reprojected(inputs, default_run):
    roads = gpd.read_file(ROADS).to_crs(4326)
    b = generate(Inputs(DEM, roads, BUILDINGS), sink=SINK)
    prov = b.metadata["provenance"]
    assert prov["inputs"]["roads"] == {
        "source": "GeoDataFrame",
        "path": None,
        "url": None,
    }
    notes = prov["extra"]["crs_notes"]
    assert len(notes) == 1 and "roads" in notes[0] and "4326" in notes[0]
    assert len(b.pipes) > 0
    assert b.metadata["crs_epsg"] == default_run[0].metadata["crs_epsg"] == 32640


def test_buildings_geodataframe_accepted(default_run):
    bld = gpd.read_file(BUILDINGS)
    b = generate(Inputs(DEM, ROADS, bld), sink=SINK)
    assert b.metadata["provenance"]["inputs"]["buildings"]["source"] == "GeoDataFrame"
    assert "crs_notes" not in b.metadata["provenance"]["extra"]
    assert len(b.pipes) == len(default_run[0].pipes)


@pytest.mark.parametrize("which", ["dem", "roads", "buildings"])
def test_missing_file_raises(which):
    paths = {"dem": DEM, "roads": ROADS, "buildings": BUILDINGS}
    paths[which] = DATA / "does_not_exist.xyz"
    with pytest.raises(ValueError, match="does_not_exist"):
        generate(Inputs(**paths), sink=SINK)
    assert manager.get_config().optimization.tmax == 8.0


def test_unprojected_dem_raises(tmp_path):
    import rasterio

    out = tmp_path / "dem4326.tif"
    with rasterio.open(DEM) as src:
        profile = src.profile
        data = src.read()
    profile.update(crs="EPSG:4326")
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(data)
    with pytest.raises(ValueError, match="projected"):
        generate(Inputs(out, ROADS, BUILDINGS), sink=SINK)


def test_invalid_override_key(inputs):
    before = manager.get_config()
    with pytest.raises(ValueError):
        generate(inputs, sink=SINK, overrides={"optimization": {"no_such_key": 1}})
    assert manager.get_config() is before


def test_invalid_override_section(inputs):
    before = manager.get_config()
    with pytest.raises(ValueError):
        generate(inputs, sink=SINK, overrides={"no_such_section": {"a": 1}})
    assert manager.get_config() is before


@pytest.mark.parametrize("bad", ["highest", (1,), ("a", "b"), 5])
def test_bad_sink(inputs, bad):
    with pytest.raises(ValueError):
        generate(inputs, sink=bad)


def test_bbox_removed(inputs):
    import inspect

    assert "bbox" not in inspect.signature(generate).parameters
    with pytest.raises(TypeError):
        generate(inputs, bbox=(0, 0, 1, 1))


def test_progress_heartbeat(default_run):
    _, calls = default_run
    rd = [f for s, f, _ in calls if s == "route_design"]
    assert rd[0] == 0.55 and rd[-1] == 0.9
    inner = rd[1:-1]
    assert len(inner) >= 2
    assert all(0.55 < f < 0.9 for f in inner)
    assert rd == sorted(rd)


def test_progress_exception_aborts_and_restores(inputs):
    seen = []

    def cb(stage, frac, msg):
        seen.append(stage)
        if stage == "route_design" and frac > 0.55:
            raise KeyboardInterrupt("cancelled")

    before = manager.get_config()
    with pytest.raises(KeyboardInterrupt, match="cancelled"):
        generate(
            inputs, sink=SINK, overrides={"optimization": {"tmax": 3}}, progress=cb
        )
    assert manager.get_config() is before
    assert "bundle" not in seen
    # the pysewer logger must not keep a progress handler
    import logging

    assert not [
        h
        for h in logging.getLogger("pysewer").handlers
        if "Heartbeat" in type(h).__name__
    ]


def test_progress_value_error_aborts(inputs):
    def cb(stage, frac, msg):
        if stage == "connect":
            raise RuntimeError("stop at connect")

    with pytest.raises(RuntimeError, match="stop at connect"):
        generate(inputs, sink=SINK, progress=cb)
    assert manager.get_config().optimization.tmax == 8.0


def test_run_provenance(default_run, tmax3_run):
    run = default_run[0].metadata["provenance"]["extra"]["run"]
    assert run == {"sink": list(map(float, SINK)), "sink_crs": None, "overrides": {}}
    run3 = tmax3_run.metadata["provenance"]["extra"]["run"]
    assert run3["overrides"] == {"optimization": {"tmax": 3}}


def test_crs_less_layer_note(inputs):
    roads = gpd.read_file(ROADS).set_crs(None, allow_override=True)
    b = generate(Inputs(DEM, roads, BUILDINGS), sink=SINK)
    notes = b.metadata["provenance"]["extra"]["crs_notes"]
    assert "roads has no CRS; the DEM CRS is assumed" in notes
    assert len(b.pipes) > 0


def test_use_config_restores():
    from pysewer.config.settings import load_config

    before = manager.get_config()
    cfg = load_config(None, {"optimization": {"tmin": 1.3}})
    with manager.use_config(cfg) as active:
        assert active is cfg
        assert manager.get_config() is cfg
    assert manager.get_config() is before


def test_use_config_restores_on_error():
    from pysewer.config.settings import load_config

    before = manager.get_config()
    cfg = load_config(None, {"optimization": {"tmin": 1.3}})
    with pytest.raises(RuntimeError), manager.use_config(cfg):
        assert manager.get_config().optimization.tmin == 1.3
        raise RuntimeError("x")
    assert manager.get_config() is before


def test_use_config_nested():
    from pysewer.config.settings import load_config

    a = load_config(None, {"optimization": {"tmin": 1.1}})
    b = load_config(None, {"optimization": {"tmin": 1.2}})
    with manager.use_config(a):
        with manager.use_config(b):
            assert manager.get_config() is b
        assert manager.get_config() is a


def test_pipeline_import_is_light():
    code = (
        "import sys, pysewer.pipeline; "
        "bad = [m for m in ('matplotlib', 'osmnx') if m in sys.modules]; "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    r = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=300
    )
    assert r.returncode == 0, r.stdout + r.stderr
