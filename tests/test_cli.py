# SPDX-License-Identifier: GPL-3.0-only
"""``pysewer.cli``: parsers, subcommands, exit codes, light import."""

import argparse
import json
import re
import subprocess
import sys
import types
import urllib.error
from pathlib import Path

import pytest

import pysewer
import pysewer.cli as cli
from pysewer.bundle import NetworkBundle
from pysewer.config import manager
from pysewer.pipeline import Inputs
from pysewer.viz.profile import longitudinal_profile
from pysewer.viz.report import report_data

DATA = Path(__file__).parent / "test_data"
DEM, ROADS, BUILDINGS = (
    str(DATA / "dem.tif"),
    str(DATA / "roads_clipped.shp"),
    str(DATA / "buildings_clipped.shp"),
)
SINK = "691350,2553250"
FILES = ["--dem", DEM, "--roads", ROADS, "--buildings", BUILDINGS]
DATA_RE = re.compile(
    r'<script type="application/json" id="pysewer-data">(.*?)</script>', re.S
)
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_config():
    manager.reset_config()
    yield
    manager.reset_config()


def _run_cli(*argv):
    return cli.main([str(a) for a in argv])


@pytest.fixture(scope="module")
def bundles(tmp_path_factory):
    """Two gpkg bundles from the test data: tmax default (a) and tmax 3 (b)."""
    d = tmp_path_factory.mktemp("bundles")
    for name, extra in (("a", []), ("b", ["--tmax", "3"])):
        manager.reset_config()
        rc = cli.main(
            [
                "run",
                *FILES,
                "--sink",
                SINK,
                "--out",
                str(d / f"{name}.gpkg"),
                *extra,
                "--progress",
                "none",
            ],
        )
        assert rc == 0
    manager.reset_config()
    return d / "a.gpkg", d / "b.gpkg"


def _embedded(html_path):
    m = DATA_RE.search(Path(html_path).read_text())
    assert m
    return json.loads(m.group(1))


# ---------------------------------------------------------------- parsers


def test_parse_bbox_order_and_floats():
    # W,S,E,N kept in the given order
    assert cli.parse_bbox("1.5,2,3.25,4") == (1.5, 2.0, 3.25, 4.0)
    assert cli.parse_bbox("-1, -2, 3, 4") == (-1.0, -2.0, 3.0, 4.0)


@pytest.mark.parametrize("text", ["1,2,3", "1,2,3,4,5", "a,b,c,d", "", "1;2;3;4"])
def test_parse_bbox_errors(text):
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_bbox(text)


def test_parse_sink():
    assert cli.parse_sink("lowest") == "lowest"
    assert cli.parse_sink(" LOWEST ") == "lowest"
    assert cli.parse_sink("691350,2553250.5") == (691350.0, 2553250.5)


@pytest.mark.parametrize("text", ["1", "1,2,3", "x,y", "highest", ""])
def test_parse_sink_errors(text):
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_sink(text)


def test_parse_overrides_empty():
    assert cli.parse_overrides() == {}
    assert cli.parse_overrides([]) == {}
    assert cli.parse_overrides(None, tmax=None) == {}


def test_parse_overrides_set_json_and_string():
    out = cli.parse_overrides(
        [
            "optimization.tmax=3",
            "optimization.flag=true",
            "optimization.list=[1, 2]",
            "optimization.pi=2.5",
            "routing.method=dijkstra",
            "routing.expr=a=b",
        ]
    )
    assert out == {
        "optimization": {"tmax": 3, "flag": True, "list": [1, 2], "pi": 2.5},
        "routing": {"method": "dijkstra", "expr": "a=b"},
    }
    assert isinstance(out["optimization"]["tmax"], int)


def test_parse_overrides_nested_section():
    assert cli.parse_overrides(["a.b.c=1"]) == {"a": {"b": {"c": 1}}}


def test_parse_overrides_convenience_flags():
    out = cli.parse_overrides(
        tmax=3.0, tmin=0.5, people_per_building=4, station_cost=100.0
    )
    assert out == {
        "optimization": {
            "tmax": 3.0,
            "tmin": 0.5,
            "inhabitants_dwelling": 4,
            "station_cost": 100.0,
        }
    }


def test_parse_overrides_set_wins_over_flag():
    out = cli.parse_overrides(["optimization.tmax=5"], tmax=3.0, tmin=0.5)
    assert out == {"optimization": {"tmax": 5, "tmin": 0.5}}


@pytest.mark.parametrize("item", ["tmax=3", "optimization.tmax", "a..b=1", ".x=1"])
def test_parse_overrides_errors(item):
    with pytest.raises(ValueError):
        cli.parse_overrides([item])


def test_parse_overrides_conflict():
    with pytest.raises(ValueError):
        cli.parse_overrides(["a.b=1", "a.b.c=2"])


# ---------------------------------------------------------------- misc


def test_console_script_entry():
    text = (REPO / "pyproject.toml").read_text()
    section = text.split("[project.scripts]")[1].split("[")[0]
    assert 'pysewer = "pysewer.cli:main"' in section


def test_version(capsys):
    assert _run_cli("--version") == 0
    assert capsys.readouterr().out.startswith("pysewer ")


def test_no_command_is_usage(capsys):
    assert _run_cli() == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_help_lists_exit_codes(capsys):
    with pytest.raises(SystemExit) as e:
        _run_cli("--help")
    assert e.value.code == 0
    out = capsys.readouterr().out
    for sub in ("run", "fetch", "report", "compare", "profile"):
        assert sub in out
    assert "130" in out


def test_light_import():
    # "import pysewer" itself loads geopandas (package design); the CLI module
    # must add nothing heavy on top, and matplotlib/osmnx stay out entirely.
    code = (
        "import sys, pysewer; before=set(sys.modules); import pysewer.cli; "
        "new={m.split('.')[0] for m in set(sys.modules)-before}; "
        "heavy=[m for m in ('geopandas','pandas','matplotlib','osmnx','plotly','pyproj') "
        "if m in new]; assert not heavy, heavy; "
        "bad=[m for m in ('matplotlib','osmnx') if m in sys.modules]; "
        "assert not bad, bad"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=REPO,
    )
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------- run


def test_run_gpkg_summary_and_name(tmp_path, capsys):
    out = tmp_path / "mynet.gpkg"
    assert _run_cli("run", *FILES, "--sink", SINK, "--out", out) == 0
    cap = capsys.readouterr()
    assert out.exists()
    assert "network mynet" in cap.out  # --name defaults to the --out stem
    assert "pipes:" in cap.out and "buildings connected:" in cap.out
    assert f"wrote {out}" in cap.out
    b = NetworkBundle.read(out)
    assert b.metadata["name"] == "mynet"
    assert cap.err  # text progress on stderr


def test_run_name_option_json_and_report(tmp_path, capsys):
    out = tmp_path / "x.gpkg"
    html = tmp_path / "r.html"
    rc = _run_cli(
        "run", *FILES, "--sink", SINK, "--out", out, "--name", "custom",
        "--report", html, "--json", "--progress", "none",
    )  # fmt: skip
    cap = capsys.readouterr()
    assert rc == 0
    assert cap.err == ""
    metrics = json.loads(cap.out)  # stdout is exactly one JSON document
    b = NetworkBundle.read(out)
    assert b.metadata["name"] == "custom"
    assert metrics["n_pipes"] == len(b.pipes)
    assert metrics["buildings_total"] == 57
    assert html.exists() and "pysewer-data" in html.read_text()
    assert _embedded(html)["title"] == "custom"


def test_run_default_name_without_out(capsys):
    assert _run_cli("run", *FILES, "--sink", SINK, "--progress", "none") == 0
    assert "network network" in capsys.readouterr().out


@pytest.mark.parametrize(
    "fmt,marker", [("geojson", "pipes.geojson"), ("parquet", "pipes.parquet")]
)
def test_run_directory_formats(tmp_path, fmt, marker):
    out = tmp_path / f"bundle_{fmt}"
    rc = _run_cli(
        "run", *FILES, "--sink", SINK, "--out", out, "--format", fmt,
        "--progress", "none",
    )  # fmt: skip
    assert rc == 0
    assert out.is_dir() and (out / marker).exists()
    assert len(NetworkBundle.read(out).pipes) > 0


def test_run_overrides_reach_config(tmp_path, bundles):
    a, b = (NetworkBundle.read(p) for p in bundles)
    assert a.metadata["config"]["optimization"]["tmax"] == 8.0
    assert b.metadata["config"]["optimization"]["tmax"] == 3.0


def test_run_set_overrides_flag(tmp_path):
    out = tmp_path / "s.gpkg"
    rc = _run_cli(
        "run", *FILES, "--sink", SINK, "--out", out, "--tmax", "6",
        "--set", "optimization.tmax=3", "--progress", "none",
    )  # fmt: skip
    assert rc == 0
    assert NetworkBundle.read(out).metadata["config"]["optimization"]["tmax"] == 3


def test_run_progress_jsonl(tmp_path, capsys):
    rc = _run_cli("run", *FILES, "--sink", SINK, "--progress", "jsonl")
    cap = capsys.readouterr()
    assert rc == 0
    recs = [json.loads(line) for line in cap.err.splitlines()]
    assert recs
    last = {}
    for r in recs:
        assert {"stage", "fraction", "message"} <= set(r)
        assert isinstance(r["stage"], str) and isinstance(r["message"], str)
        assert 0.0 <= r["fraction"] <= 1.0
        assert r["fraction"] >= last.get(r["stage"], 0.0)  # monotone per stage
        last[r["stage"]] = r["fraction"]


def test_run_progress_jsonl_envelope(capsys):
    _run_cli("run", *FILES, "--sink", SINK, "--progress", "jsonl")
    recs = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert all(r["v"] == 1 and r["phase"] == "generate" for r in recs)


def test_run_progress_none_is_silent(capsys):
    assert _run_cli("run", *FILES, "--sink", SINK, "--progress", "none") == 0
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------- report


def test_report_single(bundles, tmp_path, capsys):
    out = tmp_path / "one.html"
    assert _run_cli("report", bundles[0], "--out", out) == 0
    assert "wrote report" in capsys.readouterr().out
    data = _embedded(out)
    assert [v["name"] for v in data["variants"]] == ["a"]
    assert data["title"] == "a"


def test_report_two_bundles_comparison(bundles, tmp_path, capsys):
    out = tmp_path / "two.html"
    assert _run_cli("report", *bundles, "--out", out) == 0
    assert "comparison of 2 networks" in capsys.readouterr().out
    data = _embedded(out)
    assert [v["name"] for v in data["variants"]] == ["a", "b"]
    # the CLI embeds exactly what report_data builds (incl. the comparison)
    expected = report_data(
        {"a": NetworkBundle.read(bundles[0]), "b": NetworkBundle.read(bundles[1])}
    )
    assert data.get("comparison") == json.loads(
        json.dumps(expected.get("comparison"), allow_nan=False)
    )
    assert data["title"] == expected["title"]


def test_report_names_and_title(bundles, tmp_path):
    out = tmp_path / "n.html"
    assert (
        _run_cli(
            "report", *bundles, "--out", out, "--names", "base, alt", "--title", "T"
        )
        == 0
    )
    data = _embedded(out)
    assert [v["name"] for v in data["variants"]] == ["base", "alt"]
    assert data["title"] == "T"


def test_report_duplicate_names(bundles, tmp_path):
    out = tmp_path / "d.html"
    assert _run_cli("report", bundles[0], bundles[0], "--out", out) == 0
    assert [v["name"] for v in _embedded(out)["variants"]] == ["a", "a_2"]
    out3 = tmp_path / "d3.html"
    rc = _run_cli("report", bundles[0], bundles[0], bundles[0], "--out", out3)
    assert rc == 0
    assert [v["name"] for v in _embedded(out3)["variants"]] == ["a", "a_2", "a_3"]


def test_report_names_count_mismatch(bundles, tmp_path, capsys):
    rc = _run_cli("report", *bundles, "--out", tmp_path / "x.html", "--names", "only")
    assert rc == 3
    assert "--names" in capsys.readouterr().err


def test_report_missing_bundle(tmp_path, capsys):
    rc = _run_cli("report", tmp_path / "nope.gpkg", "--out", tmp_path / "x.html")
    assert rc == 3
    assert capsys.readouterr().err.startswith("error: bundle not found")


# ---------------------------------------------------------------- compare


def test_compare_text(bundles, capsys):
    assert _run_cli("compare", *bundles) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "Base: a"
    assert any(line.startswith("a: ") for line in lines)
    assert any(line.startswith("b against a: shares") for line in lines)
    assert "Configuration differences:" in out
    assert "optimization.tmax: a=8.0, b=3.0" in out


def test_compare_json_without_geometry(bundles, capsys):
    assert _run_cli("compare", *bundles, "--json") == 0
    d = json.loads(capsys.readouterr().out)
    assert d["schema"] == "compare/1"
    assert d["base"] == "a" and d["names"] == ["a", "b"]
    assert d["pipe_diff"] == {}


def test_compare_json_with_geometry(bundles, capsys):
    assert _run_cli("compare", *bundles, "--json", "--with-geometry") == 0
    d = json.loads(capsys.readouterr().out)
    assert set(d["pipe_diff"]) == {"b"}
    assert d["pipe_diff"]["b"]["type"] == "FeatureCollection"


def test_compare_base_and_names(bundles, capsys):
    assert _run_cli("compare", *bundles, "--names", "x,y", "--base", "y") == 0
    assert capsys.readouterr().out.splitlines()[0] == "Base: y"


# ---------------------------------------------------------------- profile


def test_profile_json_matches_python(bundles, capsys):
    assert _run_cli("profile", bundles[0], "--node", "N000115", "--json") == 0
    got = json.loads(capsys.readouterr().out)
    want = longitudinal_profile(NetworkBundle.read(bundles[0]), "N000115").to_dict()
    assert got == json.loads(json.dumps(want, allow_nan=False))
    assert got["start"] == "N000115"


def test_profile_text(bundles, capsys):
    assert _run_cli("profile", bundles[0], "--node", "N000115") == 0
    p = longitudinal_profile(NetworkBundle.read(bundles[0]), "N000115")
    out = capsys.readouterr().out
    assert out.startswith(f"profile from N000115: {len(p.segments)} pipes, ")
    assert f"ends at node {p.segments[-1].to_node}" in out


def test_profile_unknown_node(bundles, capsys):
    assert _run_cli("profile", bundles[0], "--node", "N999999") == 3
    assert capsys.readouterr().err.startswith("error:")


def test_profile_png(bundles, tmp_path, capsys):
    pytest.importorskip("matplotlib")
    png = tmp_path / "p.png"
    rc = _run_cli("profile", bundles[0], "--node", "N000115", "--png", png)
    assert rc == 0
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert f"wrote {png}" in capsys.readouterr().out


# ---------------------------------------------------------------- usage errors


@pytest.mark.parametrize(
    "argv",
    [
        ["run"],
        ["run", "--dem", DEM],
        ["run", *FILES, "--set", "tmax3"],
        ["run", *FILES, "--set", "optimization.tmax"],
        ["run", *FILES, "--sink", "somewhere"],
        ["run", *FILES, "--bbox", "1,2,3,4"],
        ["run", *FILES, "--offline"],
        ["run", "--bbox", "1,2,3"],
        ["fetch"],
        ["fetch", "--bbox", "a,b,c,d"],
        ["report", "--out", "x.html"],
        ["profile", "x.gpkg"],
        ["bogus"],
    ],
)
def test_usage_errors_exit_2(argv):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 2


def test_run_missing_file_exit_3(tmp_path, capsys):
    rc = _run_cli(
        "run", "--dem", tmp_path / "no.tif", "--roads", ROADS, "--buildings", BUILDINGS,
        "--sink", SINK, "--progress", "none",
    )  # fmt: skip
    assert rc == 3
    assert capsys.readouterr().err.startswith("error:")


# ---------------------------------------------------------------- fetch (mocked)


class FetchError(ValueError):
    pass


class AreaTooLargeError(FetchError):
    pass


class OfflineCacheMissError(FetchError, FileNotFoundError):
    pass


@pytest.fixture
def fake_sources(monkeypatch, tmp_path):
    """Replace pysewer.sources; never touch the network."""
    mod = types.ModuleType("pysewer.sources")
    mod.FetchError = FetchError
    mod.AreaTooLargeError = AreaTooLargeError
    mod.OfflineCacheMissError = OfflineCacheMissError
    mod.calls = []
    mod.behaviour = None  # exception instance to raise

    def fetch_inputs(bbox, **kwargs):
        mod.calls.append((bbox, kwargs))
        if mod.behaviour is not None:
            raise mod.behaviour
        return Inputs(
            DEM,
            ROADS,
            BUILDINGS,
            provenance={"crs": "EPSG:32640", "bbox_wgs84": list(bbox)},
        )

    mod.fetch_inputs = fetch_inputs
    monkeypatch.setitem(sys.modules, "pysewer.sources", mod)
    monkeypatch.setattr(pysewer, "sources", mod, raising=False)
    monkeypatch.setattr(cli, "_preflight", lambda dem_source, **kw: None)
    return mod


BBOX = "55.0,25.0,55.005,25.005"  # about 0.25 km2


def test_fetch_passes_options(fake_sources, capsys, tmp_path):
    rc = _run_cli(
        "fetch", "--bbox", BBOX, "--retries", "3", "--retry-wait", "0.5",
        "--cache-dir", tmp_path, "--max-area", "2", "--crs", "EPSG:32640",
        "--dem-source", "copernicus", "--progress", "none",
    )  # fmt: skip
    assert rc == 0
    ((bbox, kw),) = fake_sources.calls
    assert bbox == (55.0, 25.0, 55.005, 25.005)
    assert kw["retries"] == 3 and kw["retry_wait"] == 0.5
    assert kw["max_area_km2"] == 2.0 and kw["crs"] == "EPSG:32640"
    assert kw["offline"] is False and kw["dem_source"] == "copernicus"
    assert str(kw["cache_dir"]) == str(tmp_path)
    assert kw["progress"] is None
    out = capsys.readouterr().out
    assert f"DEM: {DEM}" in out and "CRS: EPSG:32640" in out


def test_fetch_defaults(fake_sources):
    assert _run_cli("fetch", "--bbox", BBOX) == 0
    kw = fake_sources.calls[0][1]
    assert kw["retries"] == 1 and kw["retry_wait"] == 5.0
    assert "max_area_km2" not in kw


def test_fetch_json_single_object(fake_sources, capsys):
    assert _run_cli("fetch", "--bbox", BBOX, "--json", "--progress", "none") == 0
    d = json.loads(capsys.readouterr().out)  # exactly one JSON object
    assert d["dem"] == DEM
    assert d["crs"] == "EPSG:32640"
    assert d["bbox_wgs84"] == [55.0, 25.0, 55.005, 25.005]


def test_run_with_bbox_uses_fetch(fake_sources, tmp_path, capsys):
    out = tmp_path / "f.gpkg"
    rc = _run_cli(
        "run", "--bbox", BBOX, "--sink", SINK, "--out", out, "--retries", "2",
        "--retry-wait", "0.1", "--progress", "none",
    )  # fmt: skip
    assert rc == 0
    assert out.exists()
    kw = fake_sources.calls[0][1]
    assert kw["retries"] == 2 and kw["retry_wait"] == 0.1


def test_exit_4_area_from_fetch(fake_sources, capsys):
    fake_sources.behaviour = AreaTooLargeError("box too big")
    assert _run_cli("fetch", "--bbox", BBOX) == 4
    assert capsys.readouterr().err == "error: box too big\n"


def test_exit_4_area_checked_before_preflight(fake_sources, monkeypatch, capsys):
    def boom(dem_source, **kw):
        raise cli.CliError("unreachable", 6)

    monkeypatch.setattr(cli, "_preflight", boom)
    # 0.2 x 0.2 degrees near the equator is about 490 km2
    assert _run_cli("fetch", "--bbox", "10,0,10.2,0.2") == 4
    assert "km2" in capsys.readouterr().err
    assert fake_sources.calls == []


def test_exit_4_respects_max_area(fake_sources):
    # 0.25 km2 box: fine with the default limit, too large for 0.1
    assert _run_cli("fetch", "--bbox", BBOX, "--max-area", "0.1") == 4
    assert _run_cli("fetch", "--bbox", BBOX) == 0


def test_exit_5_offline_cache_miss(fake_sources, capsys):
    fake_sources.behaviour = OfflineCacheMissError("no cache for this box")
    assert _run_cli("fetch", "--bbox", BBOX, "--offline") == 5
    assert fake_sources.calls[0][1]["offline"] is True
    assert capsys.readouterr().err == "error: no cache for this box\n"


def test_offline_skips_preflight(fake_sources, monkeypatch):
    def boom(dem_source, **kw):
        raise AssertionError("preflight must not run offline")

    monkeypatch.setattr(cli, "_preflight", boom)
    assert _run_cli("fetch", "--bbox", BBOX, "--offline") == 0


def test_exit_3_fetch_error(fake_sources, capsys):
    fake_sources.behaviour = FetchError("no buildings in the box")
    assert _run_cli("fetch", "--bbox", BBOX) == 3
    assert capsys.readouterr().err == "error: no buildings in the box\n"


def test_exit_6_preflight_message(fake_sources, monkeypatch, capsys):
    pytest.importorskip("osmnx")  # the preflight reads osmnx.settings.overpass_url
    import osmnx

    monkeypatch.undo()  # drop the mocked _preflight; keep sources mocked below
    mod = fake_sources
    monkeypatch.setitem(sys.modules, "pysewer.sources", mod)
    monkeypatch.setattr(pysewer, "sources", mod, raising=False)
    monkeypatch.setattr(
        osmnx.settings, "overpass_url", "https://overpass.example.org/api/interpreter"
    )

    def refuse(*a, **k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    rc = _run_cli("fetch", "--bbox", BBOX)
    assert rc == 6
    assert capsys.readouterr().err == (
        "error: cannot reach overpass.example.org (Overpass, Overture Maps or "
        "Copernicus DEM). Check the internet connection or use --offline with a "
        "filled cache.\n"
    )
    assert mod.calls == []


def test_preflight_http_status_counts_as_reachable(monkeypatch):
    pytest.importorskip("osmnx")  # the preflight reads osmnx.settings.overpass_url
    import osmnx

    monkeypatch.setattr(
        osmnx.settings, "overpass_url", "https://overpass.example.org/api/interpreter"
    )
    seen = []

    def answer(req, timeout=None):
        seen.append((req.full_url, req.get_method()))
        raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", answer)
    cli._preflight("copernicus")
    assert seen == [
        ("https://overpass.example.org/api/status", "GET"),
        ("https://copernicus-dem-30m.s3.amazonaws.com/", "HEAD"),
    ]


def test_exit_6_connection_error_from_fetch(fake_sources, capsys):
    requests = pytest.importorskip("requests")
    fake_sources.behaviour = requests.ConnectionError(
        "HTTPSConnectionPool(host='overpass.example.org', port=443): refused"
    )
    assert _run_cli("fetch", "--bbox", BBOX) == 6
    err = capsys.readouterr().err
    assert err.startswith("error: cannot reach overpass.example.org")
    assert "Traceback" not in err


def test_exit_1_unexpected_no_traceback(fake_sources, capsys):
    fake_sources.behaviour = RuntimeError("boom")
    assert _run_cli("fetch", "--bbox", BBOX) == 1
    err = capsys.readouterr().err
    assert err == "error: RuntimeError: boom\n"
    assert "Traceback" not in err


def test_debug_reraises(fake_sources):
    fake_sources.behaviour = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        _run_cli("--debug", "fetch", "--bbox", BBOX)
    with pytest.raises(RuntimeError, match="boom"):
        _run_cli("fetch", "--bbox", BBOX, "--debug")


def test_exit_130_keyboard_interrupt(fake_sources, capsys):
    fake_sources.behaviour = KeyboardInterrupt()
    assert _run_cli("fetch", "--bbox", BBOX) == 130
    assert "interrupted" in capsys.readouterr().err


@pytest.mark.parametrize(
    "bbox", ["10,5,10,6", "10.5,5,10,6", "10,6,11,5", "190,0,191,1", "10,-95,11,-94"]
)
def test_bad_bbox_values_exit_3(fake_sources, bbox):
    assert _run_cli("fetch", "--bbox", bbox) == 3
    assert fake_sources.calls == []


# ------------------------------------------------- source and Overpass URL
def test_fetch_passes_source_and_overpass_url(fake_sources):
    assert _run_cli("fetch", "--bbox", BBOX, "--progress", "none") == 0
    ((_, kw),) = fake_sources.calls
    assert kw["source"] == "osm" and kw["overpass_url"] is None
    fake_sources.calls.clear()
    rc = _run_cli(
        "fetch", "--bbox", BBOX, "--progress", "none",
        "--overpass-url", "https://overpass.example.org/api/interpreter",
    )  # fmt: skip
    assert rc == 0
    ((_, kw),) = fake_sources.calls
    assert kw["overpass_url"] == "https://overpass.example.org/api/interpreter"


def test_fetch_source_overture(fake_sources):
    assert _run_cli("fetch", "--bbox", BBOX, "--source", "overture") == 0
    ((_, kw),) = fake_sources.calls
    assert kw["source"] == "overture"


def test_source_must_be_known(fake_sources, capsys):
    with pytest.raises(SystemExit) as exc:
        _run_cli("fetch", "--bbox", BBOX, "--source", "here")
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_overpass_url_with_overture_is_usage_error(fake_sources, capsys):
    rc = _run_cli(
        "fetch", "--bbox", BBOX, "--source", "overture",
        "--overpass-url", "https://overpass.example.org/api",
    )  # fmt: skip
    assert rc == 2
    assert "--overpass-url applies to --source osm only" in capsys.readouterr().err
    assert fake_sources.calls == []


def test_run_with_bbox_passes_source(fake_sources, tmp_path):
    rc = _run_cli(
        "run", "--bbox", BBOX, "--source", "overture", "--sink", "lowest",
        "--progress", "none", "--out", tmp_path / "n.gpkg",
    )  # fmt: skip
    # the fake Inputs point at the test data, so the run itself may succeed
    assert fake_sources.calls[0][1]["source"] == "overture"
    assert rc in (0, 1, 3)


@pytest.mark.parametrize(
    "extra",
    [
        ["--source", "overture"],
        ["--overpass-url", "https://overpass.example.org/api"],
    ],
)
def test_run_with_files_rejects_fetch_only_options(extra, capsys):
    with pytest.raises(SystemExit) as exc:
        _run_cli(
            "run", "--dem", DEM, "--roads", ROADS, "--buildings", BUILDINGS,
            "--sink", "lowest", *extra,
        )  # fmt: skip
    assert exc.value.code == 2
    assert "need --bbox" in capsys.readouterr().err


def _record_probes(monkeypatch, status=200):
    seen = []

    def answer(req, timeout=None):
        seen.append((req.full_url, req.get_method()))
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, None)

        class R:
            def close(self):
                pass

        return R()

    monkeypatch.setattr("urllib.request.urlopen", answer)
    return seen


def test_preflight_probes_the_given_overpass_server(monkeypatch):
    seen = _record_probes(monkeypatch)
    monkeypatch.delenv("PYSEWER_OVERPASS_URL", raising=False)
    cli._preflight(
        "copernicus", overpass_url="https://overpass.example.org/api/interpreter"
    )
    assert seen == [
        ("https://overpass.example.org/api/status", "GET"),
        ("https://copernicus-dem-30m.s3.amazonaws.com/", "HEAD"),
    ]


def test_preflight_probes_the_environment_server(monkeypatch):
    seen = _record_probes(monkeypatch)
    monkeypatch.setenv("PYSEWER_OVERPASS_URL", "https://env.example.org/api")
    cli._preflight("/some/local/dem.tif")
    assert seen == [("https://env.example.org/api/status", "GET")]


def test_preflight_overture_probes_s3_not_overpass(monkeypatch):
    seen = _record_probes(monkeypatch, status=403)  # S3 answers 403 on the root
    cli._preflight("copernicus", source="overture")
    assert seen == [
        ("https://overturemaps-us-west-2.s3.amazonaws.com/", "HEAD"),
        ("https://copernicus-dem-30m.s3.amazonaws.com/", "HEAD"),
    ]


def test_preflight_overture_unreachable_is_exit_6(monkeypatch):
    def refuse(*a, **k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    with pytest.raises(cli.CliError) as exc:
        cli._preflight("copernicus", source="overture")
    assert exc.value.code == 6
    assert "overturemaps-us-west-2.s3.amazonaws.com" in str(exc.value)


def test_preflight_bad_overpass_url_is_usage_error(monkeypatch):
    _record_probes(monkeypatch)
    with pytest.raises(cli.CliError) as exc:
        cli._preflight("copernicus", overpass_url="nonsense")
    assert exc.value.code == 2


def test_fetch_preflight_receives_source_and_url(fake_sources, monkeypatch):
    got = {}

    def spy(dem_source, **kw):
        got.update(kw)

    monkeypatch.setattr(cli, "_preflight", spy)
    _run_cli(
        "fetch", "--bbox", BBOX, "--source", "osm",
        "--overpass-url", "https://overpass.example.org/api",
    )  # fmt: skip
    assert got == {"source": "osm", "overpass_url": "https://overpass.example.org/api"}
    got.clear()
    _run_cli("fetch", "--bbox", BBOX, "--source", "overture")
    assert got == {"source": "overture", "overpass_url": None}


def test_fetch_json_lists_overture_cache_files(fake_sources, tmp_path, capsys):
    fake_sources_dem = tmp_path / "dem_x.tif"
    fake_sources_dem.write_bytes(b"x")
    (tmp_path / "overture_roads_r_k.gpkg").write_bytes(b"x")

    def fetch_inputs(bbox, **kw):
        return Inputs(fake_sources_dem, ROADS, BUILDINGS, provenance={"crs": "EPSG:1"})

    fake_sources.fetch_inputs = fetch_inputs
    assert _run_cli("fetch", "--bbox", BBOX, "--source", "overture", "--json") == 0
    out = json.loads(capsys.readouterr().out)
    assert "overture_roads_r_k.gpkg" in {f["name"] for f in out["cache_files"]}


def test_overture_s3_error_is_reported_as_network_failure(fake_sources, capsys):
    fake_sources.behaviour = ConnectionError("Could not read Overture Maps from S3")
    assert _run_cli("fetch", "--bbox", BBOX, "--source", "overture") == 6
    assert "cannot reach" in capsys.readouterr().err
