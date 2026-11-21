# SPDX-FileCopyrightText: 2025 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""NetworkBundle I/O (contract bundle_io_v1): gpkg, geojson, parquet."""

import copy
import json
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

import fiona
import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import pytest
from pyproj import CRS
from shapely.geometry import LineString, Point

import pysewer
from pysewer.bundle import (
    NODE_COLUMNS,
    NODE_DTYPES,
    PIPE_COLUMNS,
    PIPE_DTYPES,
    NetworkBundle,
)
from pysewer.export import (
    export_sewer_network,
    round_float_columns,
    write_gdf_to_geojson,
    write_gdf_to_gpkg,
)
from tests.test_bundle import _synthetic

TEST_DATA = Path("tests") / "test_data"
SINK = (691350, 2553250)
FORMATS = ["gpkg", "geojson", "parquet"]

# config defaults: {"default": 3, "peak_flow": 6, "slope": 5}
DEC = {"default": 3, "peak_flow": 6, "invert_slope": 5, "ground_slope": 5}


def _digits(col):
    return DEC.get(col, DEC["default"])


def _path(tmp_path, fmt):
    return tmp_path / ("net.gpkg" if fmt == "gpkg" else f"net_{fmt}")


@pytest.fixture
def b():
    return NetworkBundle.from_graph(_synthetic(), name="syn")


@pytest.fixture(scope="module")
def real():
    md = pysewer.ModelDomain(
        TEST_DATA / "dem.tif",
        TEST_DATA / "roads_clipped.shp",
        TEST_DATA / "buildings_clipped.shp",
    )
    md.add_sink(SINK)
    cg = md.generate_connection_graph()
    sinks = md.get_sinks()
    G = pysewer.route_and_design(cg, sinks, station_cost=500, max_iterations=2)
    return NetworkBundle.from_graph(G, name="real", sinks=sinks)


def _empty():
    return NetworkBundle.from_graph(nx.DiGraph(crs=CRS("EPSG:32633")), name="empty")


# ------------------------------------------------------------------ helpers
def _assert_attrs(orig, back, exact, geom_tol=0.0):
    assert len(orig) == len(back)
    assert list(orig.columns) == list(back.columns)
    for col in orig.columns:
        if col == "geometry":
            continue
        a, c = orig[col], back[col]
        if pd.api.types.is_float_dtype(a):
            assert c.dtype == np.float64
            assert (a.isna().values == c.isna().values).all(), col
            tol = 0.0 if exact else 0.5 * 10 ** -_digits(col) + 1e-12
            ok = a.notna().values
            diff = np.abs(a.values[ok] - c.values[ok])
            assert (diff <= tol).all(), (col, diff.max(), tol)
        else:
            assert a.astype(object).where(a.notna(), None).tolist() == \
                c.astype(object).where(c.notna(), None).tolist(), col
    if geom_tol == 0.0:
        assert all(g1.equals_exact(g2, 0) for g1, g2 in zip(orig.geometry, back.geometry, strict=False))
    else:
        assert all(g1.hausdorff_distance(g2) <= geom_tol
                   for g1, g2 in zip(orig.geometry, back.geometry, strict=False))


def _assert_profiles(orig, back, atol):
    assert set(orig) == set(back)
    for pid, prof in orig.items():
        assert set(prof) == set(back[pid])
        for kind, pts in prof.items():
            a, c = np.array(pts, float), np.array(back[pid][kind], float)
            assert a.shape == c.shape
            assert np.allclose(a, c, rtol=0, atol=atol, equal_nan=True), (pid, kind)


def _assert_roundtrip(orig, back, fmt):
    exact = fmt == "parquet"
    gtol = 0.02 if fmt == "geojson" else 0.0
    _assert_attrs(orig.pipes, back.pipes, exact, gtol)
    _assert_attrs(orig.nodes, back.nodes, exact, gtol)
    assert back.crs == orig.crs
    _assert_profiles(orig.profiles, back.profiles, 0.0 if exact else 0.5e-3)
    assert back.metadata == orig.metadata


# ------------------------------------------------------------------ round trip
@pytest.mark.parametrize("fmt", FORMATS)
def test_roundtrip_synthetic(b, tmp_path, fmt):
    out = b.write(_path(tmp_path, fmt), format=fmt)
    back = NetworkBundle.read(out)
    _assert_roundtrip(b, back, fmt)


@pytest.mark.parametrize("fmt", FORMATS)
def test_roundtrip_real_tolerance_table(real, tmp_path, fmt):
    out = real.write(_path(tmp_path, fmt), format=fmt)
    back = NetworkBundle.read(out)
    _assert_roundtrip(real, back, fmt)
    assert len(back.pipes) == real.metadata["counts"]["pipes"] > 0


@pytest.mark.parametrize("fmt", FORMATS)
def test_dtypes_and_order_after_read(b, tmp_path, fmt):
    back = NetworkBundle.read(b.write(_path(tmp_path, fmt), format=fmt))
    assert list(back.pipes.columns) == [*PIPE_COLUMNS, "geometry"]
    assert list(back.nodes.columns) == [*NODE_COLUMNS, "geometry"]
    ref_p = b.pipes.dtypes.astype(str).to_dict()
    ref_n = b.nodes.dtypes.astype(str).to_dict()
    assert back.pipes.dtypes.astype(str).to_dict() == ref_p
    assert back.nodes.dtypes.astype(str).to_dict() == ref_n
    assert str(back.nodes["upstream_pe"].dtype) == "Int64"
    assert back.nodes["upstream_pe"].isna().any()  # K, ISO have no upstream_pe
    assert back.nodes["upstream_pe"].tolist()[:2] == [6, 6]
    assert str(back.pipes["n_violations"].dtype) == "int64"
    for df, dt in ((back.pipes, PIPE_DTYPES), (back.nodes, NODE_DTYPES)):
        for col, kind in dt.items():
            if kind == "bool":
                assert df[col].dtype == bool
                assert not df[col].isna().any()
            if kind == "float64":
                assert df[col].dtype == np.float64
    assert isinstance(back.pipes.index, pd.RangeIndex)
    assert isinstance(back.nodes.index, pd.RangeIndex)
    # strings: no NaN, empty string for missing text
    assert back.pipes["road_name"].tolist() == b.pipes["road_name"].tolist()
    assert not back.pipes["highway"].isna().any()


@pytest.mark.parametrize("fmt", FORMATS)
def test_all_nan_float_columns_stay_float(b, tmp_path, fmt):
    nodes = b.nodes.copy()
    nodes["population"] = np.nan
    nodes["static_head"] = np.nan
    pipes = b.pipes.copy()
    pipes["velocity"] = np.nan
    nb = NetworkBundle(pipes=pipes, nodes=nodes, metadata=b.metadata, profiles=b.profiles)
    back = NetworkBundle.read(nb.write(_path(tmp_path, fmt), format=fmt))
    assert back.nodes["population"].dtype == np.float64
    assert back.nodes["static_head"].dtype == np.float64
    assert back.pipes["velocity"].dtype == np.float64
    assert back.nodes["population"].isna().all()


def test_gpkg_all_nan_float_schema(b, tmp_path):
    nodes = b.nodes.copy()
    nodes["population"] = np.nan
    nb = NetworkBundle(pipes=b.pipes, nodes=nodes, metadata=b.metadata, profiles=b.profiles)
    out = nb.to_gpkg(tmp_path / "n.gpkg")
    with fiona.open(out, layer="nodes") as src:
        assert src.schema["properties"]["population"].startswith("float")
        assert src.schema["properties"]["upstream_pe"].startswith("int")
        assert src.schema["properties"]["onsite"].startswith("bool")


def test_slope_alias_rounding_gpkg(b, tmp_path):
    pipes = b.pipes.copy()
    pipes.loc[0, "invert_slope"] = 0.0123456789
    pipes.loc[0, "ground_slope"] = 0.0314159265
    pipes.loc[0, "length"] = 140.123456
    nb = NetworkBundle(pipes=pipes, nodes=b.nodes, metadata=b.metadata, profiles=b.profiles)
    back = NetworkBundle.read(nb.to_gpkg(tmp_path / "a.gpkg"))
    # slope config = 5 decimals: 0.0123456789 -> 0.01235, 0.0314159265 -> 0.03142
    assert back.pipes.loc[0, "invert_slope"] == pytest.approx(0.01235, abs=1e-12)
    assert back.pipes.loc[0, "ground_slope"] == pytest.approx(0.03142, abs=1e-12)
    # default 3 decimals: 140.123456 -> 140.123
    assert back.pipes.loc[0, "length"] == pytest.approx(140.123, abs=1e-12)


def test_peak_flow_six_decimals(b, tmp_path):
    pipes = b.pipes.copy()
    pipes.loc[0, "peak_flow"] = 0.0012345678
    nb = NetworkBundle(pipes=pipes, nodes=b.nodes, metadata=b.metadata, profiles=b.profiles)
    back = NetworkBundle.read(nb.to_gpkg(tmp_path / "a.gpkg"))
    assert back.pipes.loc[0, "peak_flow"] == pytest.approx(0.001235, abs=1e-12)  # 6 decimals


def test_in_memory_bundle_not_rounded(b, tmp_path):
    pipes = b.pipes.copy()
    pipes.loc[0, "length"] = 140.123456
    nb = NetworkBundle(pipes=pipes, nodes=b.nodes, metadata=b.metadata, profiles=b.profiles)
    before = copy.deepcopy(nb.profiles)
    nb.to_gpkg(tmp_path / "a.gpkg")
    nb.to_geojson(tmp_path / "g")
    assert nb.pipes.loc[0, "length"] == 140.123456
    assert nb.profiles == before


def test_parquet_lossless_exact(b, tmp_path):
    pipes = b.pipes.copy()
    pipes.loc[0, "length"] = 140.123456789
    prof = copy.deepcopy(b.profiles)
    prof["P000001"]["ground"][0][1] = 110.123456789
    nb = NetworkBundle(pipes=pipes, nodes=b.nodes, metadata=b.metadata, profiles=prof)
    back = NetworkBundle.read(nb.to_parquet(tmp_path / "p"))
    assert back.pipes.loc[0, "length"] == 140.123456789
    assert back.profiles["P000001"]["ground"][0][1] == 110.123456789


def test_profiles_rounded_in_gpkg(b, tmp_path):
    prof = copy.deepcopy(b.profiles)
    prof["P000001"]["ground"][0][1] = 110.123456
    nb = NetworkBundle(pipes=b.pipes, nodes=b.nodes, metadata=b.metadata, profiles=prof)
    back = NetworkBundle.read(nb.to_gpkg(tmp_path / "a.gpkg"))
    assert back.profiles["P000001"]["ground"][0][1] == pytest.approx(110.123, abs=1e-12)


def test_profiles_nan_roundtrip(b, tmp_path):
    prof = copy.deepcopy(b.profiles)
    prof["P000001"]["ground"][1][1] = float("nan")
    nb = NetworkBundle(pipes=b.pipes, nodes=b.nodes, metadata=b.metadata, profiles=prof)
    for fmt in FORMATS:
        out = nb.write(_path(tmp_path, fmt), format=fmt)
        back = NetworkBundle.read(out)
        assert np.isnan(back.profiles["P000001"]["ground"][1][1])
        assert back.profiles["P000001"]["ground"][0][1] == 110.0
    text = (tmp_path / "net_geojson" / "profiles.json").read_text()
    assert "NaN" not in text
    assert json.loads(text)["P000001"]["ground"][1][1] is None


# ------------------------------------------------------------------ GPKG layout
def test_gpkg_layers_and_contents(b, tmp_path):
    out = b.to_gpkg(tmp_path / "a.gpkg")
    assert isinstance(out, Path)
    assert set(fiona.listlayers(out)) == {"pipes", "nodes", "pysewer_metadata"}
    con = sqlite3.connect(out)
    try:
        types = dict(con.execute("select table_name, data_type from gpkg_contents"))
    finally:
        con.close()
    assert types["pysewer_metadata"] == "attributes"
    assert types["pipes"] == "features"
    assert types["nodes"] == "features"
    with fiona.open(out, layer="pysewer_metadata") as src:
        assert src.schema["geometry"] == "None"
        rows = {f["properties"]["key"]: f["properties"]["value"] for f in src}
    assert set(rows) == {"metadata", "profiles"}
    assert json.loads(rows["metadata"]) == b.metadata


def test_gpkg_crs_is_projected(b, tmp_path):
    out = b.to_gpkg(tmp_path / "a.gpkg")
    assert gpd.read_file(out, layer="pipes").crs == CRS("EPSG:32633")
    assert gpd.read_file(out, layer="nodes").crs == CRS("EPSG:32633")


def test_to_gpkg_replaces_existing(b, tmp_path):
    out = tmp_path / "a.gpkg"
    gpd.GeoDataFrame({"x": [1]}, geometry=[Point(0, 0)], crs=32633).to_file(
        out, layer="stale", driver="GPKG")
    b.to_gpkg(out)
    assert "stale" not in fiona.listlayers(out)
    b.to_gpkg(out)  # twice: no duplicate rows
    assert len(NetworkBundle.read(out).pipes) == 4


# ------------------------------------------------------------------ GeoJSON
def test_geojson_files_and_rfc7946(b, tmp_path):
    d = b.to_geojson(tmp_path / "g")
    assert Path(d) == tmp_path / "g"
    assert sorted(p.name for p in d.iterdir()) == [
        "metadata.json", "nodes.geojson", "pipes.geojson", "profiles.json"]
    doc = json.loads((d / "pipes.geojson").read_text())
    assert "crs" not in doc
    # synthetic: x = 500000 is the central meridian of zone 33 -> lon 15 E exactly
    lon, lat = doc["features"][0]["geometry"]["coordinates"][0]
    assert lon == pytest.approx(15.0, abs=1e-7)
    assert 45.0 < lat < 45.2  # 5000000 m N is about 45.1 N
    for f in doc["features"]:
        for x, y in f["geometry"]["coordinates"]:
            for v in (x, y):
                assert len(repr(v).split(".")[-1]) <= 7


def test_geojson_real_location_and_precision(real, tmp_path):
    d = real.to_geojson(tmp_path / "g")
    doc = json.loads((d / "pipes.geojson").read_text())
    lon, lat = doc["features"][0]["geometry"]["coordinates"][0]
    assert abs(lon - 58.87) < 0.5 and abs(lat - 23.07) < 0.5
    for f in doc["features"]:
        for x, y in f["geometry"]["coordinates"]:
            for v in (x, y):
                assert len(repr(v).split(".")[-1]) <= 7


def test_geojson_null_and_bool(b, tmp_path):
    d = b.to_geojson(tmp_path / "g")
    pipes = json.loads((d / "pipes.geojson").read_text())
    nodes = json.loads((d / "nodes.geojson").read_text())
    for text in ((d / "pipes.geojson").read_text(), (d / "nodes.geojson").read_text()):
        assert "NaN" not in text and "Infinity" not in text
    undes = next(f for f in pipes["features"] if f["properties"]["pipe_id"] == "P000004")
    assert undes["properties"]["diameter"] is None
    assert undes["properties"]["private"] is False
    first = next(f for f in pipes["features"] if f["properties"]["pipe_id"] == "P000001")
    assert first["properties"]["private"] is True
    n1 = next(f for f in nodes["features"] if f["properties"]["node_id"] == "N000001")
    assert n1["properties"]["pumping_station"] is True
    assert n1["properties"]["onsite"] is False
    n6 = next(f for f in nodes["features"] if f["properties"]["node_id"] == "N000006")
    assert n6["properties"]["upstream_pe"] is None
    assert n6["properties"]["onsite"] is True
    for f in nodes["features"]:
        for k in ("pumping_station", "lifting_station", "private_pump", "onsite", "unrouted"):
            assert isinstance(f["properties"][k], bool)


def test_geojson_metadata_json(b, tmp_path):
    d = b.to_geojson(tmp_path / "g")
    meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
    assert meta["crs_epsg"] == 4326
    assert CRS.from_wkt(meta["source_crs_wkt"]) == CRS("EPSG:32633")
    assert meta["counts"] == {"pipes": 4, "nodes": 6}
    back = NetworkBundle.read(d)
    assert back.crs == CRS("EPSG:32633")
    assert back.metadata["crs_epsg"] == 32633
    assert "source_crs_wkt" not in back.metadata
    assert CRS.from_wkt(back.metadata["crs_wkt"]) == CRS("EPSG:32633")


def test_geojson_read_without_source_crs_stays_4326(b, tmp_path):
    d = b.to_geojson(tmp_path / "g")
    meta = json.loads((d / "metadata.json").read_text())
    del meta["source_crs_wkt"]
    (d / "metadata.json").write_text(json.dumps(meta))
    back = NetworkBundle.read(d)
    assert back.crs == CRS("EPSG:4326")


def test_geojson_no_crs_raises(b, tmp_path):
    nb = NetworkBundle(
        pipes=b.pipes.set_crs(None, allow_override=True),
        nodes=b.nodes.set_crs(None, allow_override=True),
        metadata=b.metadata, profiles=b.profiles)
    with pytest.raises(ValueError):
        nb.to_geojson(tmp_path / "g")


def test_json_dirs_keep_other_files_and_overwrite(b, tmp_path):
    for fmt in ("geojson", "parquet"):
        d = tmp_path / fmt
        d.mkdir()
        (d / "keep.txt").write_text("x")
        getattr(b, f"to_{fmt}")(d)
        getattr(b, f"to_{fmt}")(d)  # overwrite works
        assert (d / "keep.txt").read_text() == "x"
    nested = tmp_path / "a" / "b" / "c"
    b.to_parquet(nested)
    assert (nested / "pipes.parquet").exists()


def test_parquet_files(b, tmp_path):
    d = b.to_parquet(tmp_path / "p")
    assert sorted(p.name for p in d.iterdir()) == [
        "metadata.json", "nodes.parquet", "pipes.parquet", "profiles.json"]
    assert gpd.read_parquet(d / "pipes.parquet").crs == CRS("EPSG:32633")


# ------------------------------------------------------------------ empty graph
@pytest.mark.parametrize("fmt", FORMATS)
def test_empty_roundtrip(tmp_path, fmt):
    e = _empty()
    assert len(e.pipes) == 0 and len(e.nodes) == 0
    back = NetworkBundle.read(e.write(_path(tmp_path, fmt), format=fmt))
    assert len(back.pipes) == 0 and len(back.nodes) == 0
    assert list(back.pipes.columns) == [*PIPE_COLUMNS, "geometry"]
    assert list(back.nodes.columns) == [*NODE_COLUMNS, "geometry"]
    assert back.crs == CRS("EPSG:32633")
    assert back.metadata == e.metadata
    assert back.profiles == {}
    assert str(back.nodes["upstream_pe"].dtype) == "Int64"
    assert back.pipes["length"].dtype == np.float64


# ------------------------------------------------------------------ read errors
def test_read_autodetect(b, tmp_path):
    p = b.write(tmp_path / "x.gpkg")
    assert len(NetworkBundle.read(p).pipes) == 4
    assert len(NetworkBundle.read(b.to_parquet(tmp_path / "p")).pipes) == 4
    assert len(NetworkBundle.read(b.to_geojson(tmp_path / "g")).pipes) == 4
    assert len(NetworkBundle.read(str(tmp_path / "p")).pipes) == 4  # str path


def test_read_explicit_format(b, tmp_path):
    d = b.to_parquet(tmp_path / "p")
    b.to_geojson(d)  # both present
    assert len(NetworkBundle.read(d, format="parquet").pipes) == 4
    assert len(NetworkBundle.read(d, format="geojson").pipes) == 4


def test_read_both_files_ambiguous(b, tmp_path):
    d = b.to_parquet(tmp_path / "d")
    b.to_geojson(d)
    with pytest.raises(ValueError):
        NetworkBundle.read(d)


def test_read_neither_file(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError):
        NetworkBundle.read(tmp_path / "empty")


def test_read_missing_path(tmp_path):
    with pytest.raises((FileNotFoundError, ValueError)):
        NetworkBundle.read(tmp_path / "nope")


def test_read_unknown_format(b, tmp_path):
    d = b.to_parquet(tmp_path / "p")
    with pytest.raises(ValueError):
        NetworkBundle.read(d, format="shp")


def _edit_meta_parquet(b, tmp_path, edit):
    d = b.to_parquet(tmp_path / "p")
    meta = json.loads((d / "metadata.json").read_text())
    edit(meta)
    (d / "metadata.json").write_text(json.dumps(meta))
    return d


def _drop_version(m):
    del m["schema_version"]


@pytest.mark.parametrize("edit", [_drop_version,
                                  lambda m: m.update(schema_version="1"),
                                  lambda m: m.update(schema_version="2.0"),
                                  lambda m: m.update(schema_version="abc"),
                                  lambda m: m.update(schema_version=1.0)],
                         ids=["missing", "1", "2.0", "abc", "float"])
def test_schema_version_errors_parquet(b, tmp_path, edit):
    d = _edit_meta_parquet(b, tmp_path, edit)
    with pytest.raises(ValueError):
        NetworkBundle.read(d)


def test_schema_version_errors_gpkg(b, tmp_path):
    out = b.to_gpkg(tmp_path / "a.gpkg")
    meta = dict(b.metadata, schema_version="2.0")
    _rewrite_gpkg_meta(out, meta)
    with pytest.raises(ValueError):
        NetworkBundle.read(out)


def _rewrite_gpkg_meta(path, meta):
    con = sqlite3.connect(path)
    try:
        con.execute("update pysewer_metadata set value=? where key='metadata'",
                    (json.dumps(meta),))
        con.commit()
    finally:
        con.close()


def test_newer_minor_warns_and_reads(b, tmp_path, caplog):
    d = _edit_meta_parquet(b, tmp_path, lambda m: m.update(schema_version="1.9"))
    with caplog.at_level(logging.WARNING):
        back = NetworkBundle.read(d)
    assert len(back.pipes) == 4
    assert back.metadata["schema_version"] == "1.9"
    assert any("1.9" in r.getMessage() for r in caplog.records
               if r.levelno == logging.WARNING)


def test_same_version_no_warning(b, tmp_path, caplog):
    d = b.to_parquet(tmp_path / "p")
    with caplog.at_level(logging.WARNING):
        NetworkBundle.read(d)
    assert not [r for r in caplog.records
                if r.levelno >= logging.WARNING and "schema_version" in r.getMessage()]


@pytest.mark.parametrize("fmt", FORMATS)
def test_counts_mismatch_raises(b, tmp_path, fmt):
    out = b.write(_path(tmp_path, fmt), format=fmt)
    meta = copy.deepcopy(b.metadata)
    meta["counts"] = {"pipes": 5, "nodes": 6}
    if fmt == "gpkg":
        _rewrite_gpkg_meta(out, meta)
    else:
        m = json.loads((out / "metadata.json").read_text())
        m["counts"] = meta["counts"]
        (out / "metadata.json").write_text(json.dumps(m))
    with pytest.raises(ValueError):
        NetworkBundle.read(out)


def _filtered(b):
    keep = b.pipes.iloc[:2].reset_index(drop=True)
    ids = set(keep.from_node) | set(keep.to_node)
    nodes = b.nodes[b.nodes.node_id.isin(ids)].reset_index(drop=True)
    return NetworkBundle(pipes=keep, nodes=nodes, metadata=b.metadata,
                         profiles=b.profiles), len(keep), len(nodes)


@pytest.mark.parametrize("fmt", FORMATS)
def test_writers_refresh_counts_on_filtered_bundle(b, tmp_path, fmt):
    fb, npipes, nnodes = _filtered(b)
    assert (npipes, nnodes) == (2, 3)  # P1: N1-N2, P2: N2-N3
    before = copy.deepcopy(b.metadata["counts"])
    back = NetworkBundle.read(fb.write(_path(tmp_path, fmt), format=fmt))
    assert back.metadata["counts"] == {"pipes": 2, "nodes": 3}
    assert len(back.pipes) == 2 and len(back.nodes) == 3
    assert b.metadata["counts"] == before == {"pipes": 4, "nodes": 6}
    assert fb.metadata["counts"] == before  # bundle not mutated


def test_to_geojson_dict(b, tmp_path):
    d = b.to_geojson_dict()
    assert set(d) == {"pipes", "nodes", "metadata", "profiles"}
    for k, n in (("pipes", 4), ("nodes", 6)):
        assert d[k]["type"] == "FeatureCollection"
        assert len(d[k]["features"]) == n
    json.dumps(d, allow_nan=False)
    for f in d["pipes"]["features"]:
        for x, y in f["geometry"]["coordinates"]:
            assert 14.9 < x < 15.1 and 45.0 < y < 45.3
            for v in (x, y):
                assert len(repr(v).split(".")[-1]) <= 7
    assert d["metadata"]["crs_epsg"] == 4326
    assert CRS.from_wkt(d["metadata"]["source_crs_wkt"]) == CRS("EPSG:32633")
    assert d["metadata"]["counts"] == {"pipes": 4, "nodes": 6}
    # same content as the files
    out = b.to_geojson(tmp_path / "g")
    for k in ("pipes", "nodes"):
        doc = json.loads((out / f"{k}.geojson").read_text())
        assert len(doc["features"]) == len(d[k]["features"])
        assert [f["properties"] for f in doc["features"]] == \
            [f["properties"] for f in d[k]["features"]]
        assert [f["geometry"] for f in doc["features"]] == \
            [f["geometry"] for f in d[k]["features"]]
    assert json.loads((out / "metadata.json").read_text()) == d["metadata"]
    assert json.loads((out / "profiles.json").read_text()) == d["profiles"]
    assert len(gpd.read_file(out / "pipes.geojson")) == len(d["pipes"]["features"])


def test_to_geojson_dict_profiles_none_not_nan(b):
    prof = copy.deepcopy(b.profiles)
    prof["P000001"]["ground"][1][1] = float("nan")
    nb = NetworkBundle(pipes=b.pipes, nodes=b.nodes, metadata=b.metadata, profiles=prof)
    d = nb.to_geojson_dict()
    assert d["profiles"]["P000001"]["ground"][1][1] is None
    json.dumps(d, allow_nan=False)
    assert np.isnan(prof["P000001"]["ground"][1][1])  # original untouched


def test_to_geojson_dict_filtered_counts_and_no_crs(b):
    fb, _, _ = _filtered(b)
    assert fb.to_geojson_dict()["metadata"]["counts"] == {"pipes": 2, "nodes": 3}
    nb = NetworkBundle(
        pipes=b.pipes.set_crs(None, allow_override=True),
        nodes=b.nodes.set_crs(None, allow_override=True),
        metadata=b.metadata, profiles=b.profiles)
    with pytest.raises(ValueError):
        nb.to_geojson_dict()


def test_known_column_missing_raises(b, tmp_path):
    d = b.to_parquet(tmp_path / "p")
    g = gpd.read_parquet(d / "pipes.parquet").drop(columns=["diameter"])
    g.to_parquet(d / "pipes.parquet", index=False)
    with pytest.raises(ValueError):
        NetworkBundle.read(d)


def test_extra_columns_kept_after_known_before_geometry(b, tmp_path):
    d = b.to_parquet(tmp_path / "p")
    g = gpd.read_parquet(d / "nodes.parquet")
    g["zzz"] = 1.5
    g["aaa"] = "q"
    g.to_parquet(d / "nodes.parquet", index=False)
    back = NetworkBundle.read(d)
    assert list(back.nodes.columns) == [*NODE_COLUMNS, "zzz", "aaa", "geometry"]


def test_reordered_columns_are_conformed(b, tmp_path):
    d = b.to_parquet(tmp_path / "p")
    g = gpd.read_parquet(d / "nodes.parquet")
    cols = list(reversed([c for c in g.columns if c != "geometry"]))
    g[[*cols, "geometry"]].to_parquet(d / "nodes.parquet", index=False)
    back = NetworkBundle.read(d)
    assert list(back.nodes.columns) == [*NODE_COLUMNS, "geometry"]


# ------------------------------------------------------------------ write()
def test_write_dispatch_and_return(b, tmp_path):
    p = b.write(tmp_path / "a.gpkg")
    assert p == tmp_path / "a.gpkg" and p.is_file()
    p = b.write(tmp_path / "gdir", format="geojson")
    assert p.is_dir() and (p / "pipes.geojson").exists()
    p = b.write(str(tmp_path / "pdir"), format="parquet")
    assert Path(p).is_dir() and (Path(p) / "pipes.parquet").exists()


def test_write_format_overrides_suffix(b, tmp_path):
    p = b.write(tmp_path / "weird.gpkg", format="parquet")
    assert p.is_dir() and (p / "pipes.parquet").exists()


def test_write_no_suffix_needs_format(b, tmp_path):
    with pytest.raises(ValueError, match="format"):
        b.write(tmp_path / "noext")
    with pytest.raises(ValueError):
        b.write(tmp_path / "x.parquet")
    with pytest.raises(ValueError):
        b.write(tmp_path / "x.geojson")


def test_write_unknown_format(b, tmp_path):
    with pytest.raises(ValueError):
        b.write(tmp_path / "x", format="shp")


def test_write_uppercase_gpkg_suffix(b, tmp_path):
    assert b.write(tmp_path / "A.GPKG").is_file()


# ------------------------------------------------------------------ light import
def test_import_pysewer_stays_light():
    code = (
        "import sys, pysewer; "
        "bad = [m for m in ('matplotlib', 'osmnx') if m in sys.modules]; "
        "assert not bad, bad"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ------------------------------------------------------------------ export.py
@pytest.fixture
def egdf():
    return gpd.GeoDataFrame(
        {
            "private_sewer": [True, np.nan, True],
            "violations": [["max_slope"], np.nan, []],
            "count": [1, 2, 3],
            "diameter": [0.2, np.nan, 0.3],
            "name": ["a", None, "c"],
        },
        geometry=[LineString([(500000, 5000000 + i), (500010, 5000000 + i)]) for i in range(3)],
        crs="EPSG:32633",
    )


def test_round_float_columns_default_unchanged():
    g = gpd.GeoDataFrame(
        {"length": [1.23456], "peak_flow": [0.1234567891], "slope": [0.123456789]},
        geometry=[Point(0, 0)], crs=32633)
    out = round_float_columns(g.copy())
    assert out.loc[0, "length"] == 1.235  # default 3
    assert out.loc[0, "peak_flow"] == 0.123457  # 6
    assert out.loc[0, "slope"] == 0.12346  # 5


def test_round_float_columns_override():
    g = gpd.GeoDataFrame({"a": [1.23456], "b": [1.23456]}, geometry=[Point(0, 0)])
    out = round_float_columns(g.copy(), {"a": 1, "default": 2})
    assert out.loc[0, "a"] == 1.2
    assert out.loc[0, "b"] == 1.23
    off = round_float_columns(g.copy(), {})
    assert off.loc[0, "a"] == 1.23456
    only = round_float_columns(g.copy(), {"a": 4})
    assert only.loc[0, "a"] == 1.2346
    assert only.loc[0, "b"] == 1.23456  # no default: untouched


def test_write_gpkg_decimals_override_and_field_types(tmp_path):
    g = gpd.GeoDataFrame({"v": [np.nan, np.nan], "w": [1.23456, 2.0]},
                         geometry=[Point(0, 0), Point(1, 1)], crs=32633)
    out = tmp_path / "a.gpkg"
    write_gdf_to_gpkg(g, str(out), layer="n", decimals={"default": 1},
                      field_types={"v": "float"})
    with fiona.open(out, layer="n") as src:
        assert src.schema["properties"]["v"].startswith("float")
        vals = [f["properties"]["w"] for f in src]
    assert vals == [1.2, 2.0]


def test_write_gpkg_empty_with_geometry_type(tmp_path):
    g = gpd.GeoDataFrame({"v": pd.Series([], dtype=float)}, geometry=[], crs=32633)
    out = tmp_path / "a.gpkg"
    write_gdf_to_gpkg(g, str(out), layer="n", geometry_type="Point",
                      field_types={"v": "float"})
    assert len(gpd.read_file(out, layer="n")) == 0


def test_write_gpkg_defaults_unchanged(egdf, tmp_path):
    out = tmp_path / "a.gpkg"
    write_gdf_to_gpkg(egdf, str(out), layer="pipes")
    back = gpd.read_file(out, layer="pipes")
    assert back["diameter"].tolist()[0] == 0.2
    assert back["violations"].tolist()[0] == '["max_slope"]'
    assert back.crs == CRS("EPSG:32633")


@pytest.mark.parametrize("fmt", ["gpkg", "geojson", "parquet"])
def test_exports_carry_crs(egdf, tmp_path, fmt):
    path = tmp_path / f"pipes.{fmt}"
    export_sewer_network(egdf, str(path), file_format=fmt)
    back = gpd.read_parquet(path) if fmt == "parquet" else gpd.read_file(path)
    expected = CRS("EPSG:4326") if fmt == "geojson" else egdf.crs
    assert back.crs == expected


def test_export_geojson_from_config(egdf, tmp_path):
    from pysewer.config import manager
    cfg = manager.get_config()
    old = cfg.export.file_format
    cfg.export.file_format = "geojson"
    try:
        path = tmp_path / "pipes.geojson"
        export_sewer_network(egdf, str(path))
    finally:
        cfg.export.file_format = old
    assert gpd.read_file(path).crs == CRS("EPSG:4326")


def test_export_geojson_lists_nan_null(egdf, tmp_path):
    path = tmp_path / "pipes.geojson"
    export_sewer_network(egdf, str(path), file_format="geojson")
    text = path.read_text()
    assert "NaN" not in text
    doc = json.loads(text)
    props = [f["properties"] for f in doc["features"]]
    assert [p["violations"] for p in props] == [["max_slope"], None, []]  # GDAL writes native arrays
    assert props[1]["diameter"] is None
    assert props[1]["private_sewer"] is None
    assert props[0]["private_sewer"] is True
    assert props[1]["name"] is None
    assert "crs" not in doc


def test_write_gdf_to_geojson_reprojects(egdf, tmp_path):
    path = tmp_path / "x.geojson"
    write_gdf_to_geojson(egdf, str(path))
    doc = json.loads(path.read_text())
    lon, lat = doc["features"][0]["geometry"]["coordinates"][0]
    assert lon == pytest.approx(15.0, abs=1e-7)  # x=500000 is the central meridian
    assert 45.0 < lat < 45.2


def test_write_gdf_to_geojson_no_crs(egdf, tmp_path):
    g = egdf.set_crs(None, allow_override=True)
    with pytest.raises(ValueError):
        write_gdf_to_geojson(g, str(tmp_path / "x.geojson"))


def test_write_gdf_to_geojson_overwrites(egdf, tmp_path):
    path = tmp_path / "x.geojson"
    write_gdf_to_geojson(egdf, str(path))
    write_gdf_to_geojson(egdf.iloc[:1], str(path))
    assert len(gpd.read_file(path)) == 1


def test_export_unsupported_format_still_raises(egdf, tmp_path):
    with pytest.raises(ValueError):
        export_sewer_network(egdf, str(tmp_path / "x.foo"), file_format="foo")
