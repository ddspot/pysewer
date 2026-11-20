# SPDX-FileCopyrightText: 2025 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""Self-contained HTML report (report/1) and NetworkBundle.to_geojson_dict."""

import json
import re
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

import networkx as nx
import pytest
from pyproj import CRS

import pysewer
from pysewer.bundle import NetworkBundle
from pysewer.config.manager import reset_config
from pysewer.metrics import network_metrics, quality_checks, summarize_findings
from pysewer.viz.profile import longitudinal_profile
from pysewer.viz.report import CDN, _longest_path_start, build_report, report_data
from tests.test_bundle import _synthetic

TEST_DATA = Path("tests") / "test_data"
SINK = (691350, 2553250)
REPO = Path(__file__).resolve().parent.parent
JS = REPO / "pysewer" / "viz" / "report" / "report.js"
DATA_RE = re.compile(
    r'<script type="application/json" id="pysewer-data">(.*?)</script>', re.S
)


@pytest.fixture(autouse=True)
def _clean_config():
    reset_config()
    yield
    reset_config()


@pytest.fixture(scope="module")
def syn():
    return NetworkBundle.from_graph(_synthetic(), name="syn")


@pytest.fixture(scope="module")
def real():
    reset_config()
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


@pytest.fixture(params=["syn", "real"])
def bundle(request, syn, real):
    return syn if request.param == "syn" else real


def embedded(html_path):
    text = Path(html_path).read_text(encoding="utf-8")
    m = DATA_RE.search(text)
    assert m, "pysewer-data script not found"
    return text, m.group(1), json.loads(m.group(1))


# ------------------------------------------------------------- build_report
def test_build_report_contract(bundle, tmp_path):
    out = build_report(bundle, tmp_path / "sub" / "r.html")
    assert out == tmp_path / "sub" / "r.html" and out.is_file()
    assert [p.name for p in out.parent.iterdir()] == ["r.html"]  # single file
    text, raw, data = embedded(out)
    assert text.count('id="pysewer-data"') == 1
    assert "</script" not in raw and "</" not in raw  # escaped as <\/
    assert "<!--" not in raw

    assert data["schema"] == "report/1"
    assert data["title"] == bundle.name
    assert len(data["variants"]) == 1
    v = data["variants"][0]
    assert set(v) == {"name", "geojson", "metrics", "findings", "summary", "crs_area"}
    assert v["name"] == bundle.name

    start = _longest_path_start(bundle)
    assert start is not None
    assert data["default_profile"] == json.loads(
        json.dumps(longitudinal_profile(bundle, start).to_dict())
    )
    assert v["metrics"] == json.loads(json.dumps(network_metrics(bundle)))
    findings = quality_checks(bundle, "bundle")
    assert len(v["findings"]) == len(findings)
    assert v["summary"] == json.loads(
        json.dumps(summarize_findings(findings, "bundle", bundle=bundle))
    )


def test_longest_path_start_is_longest(syn):
    # synthetic: A->B 140, B->P 120, P->S 140 => A is 400 m from the sink; K->S is 50 m.
    start = _longest_path_start(syn)
    row = syn.nodes[syn.nodes.node_id == start].iloc[0]
    assert (row.geometry.x, row.geometry.y) == (500000, 5000000)
    assert longitudinal_profile(syn, start).to_dict()["total_length"] == pytest.approx(
        400.0, abs=1e-9
    )


def test_geojson_pipe_lengths_unrounded(bundle, tmp_path):
    _, _, data = embedded(build_report(bundle, tmp_path / "r.html"))
    feats = data["variants"][0]["geojson"]["pipes"]["features"]
    by_id = {f["properties"]["pipe_id"]: f["properties"]["length"] for f in feats}
    assert len(by_id) == len(bundle.pipes)
    for pid, ln in zip(bundle.pipes["pipe_id"], bundle.pipes["length"], strict=True):
        if ln == ln:
            assert by_id[pid] == float(ln)  # exact, not rounded


def test_report_data_is_what_is_embedded(syn, tmp_path):
    d = report_data(syn, title="T")
    out = build_report(syn, tmp_path / "r.html", title="T")
    a, b = embedded(out)[2], json.loads(json.dumps(d))
    for x in (a, b):  # FeatureCollection "name" is a random temp-file stem
        for v in x["variants"]:
            for k in ("pipes", "nodes"):
                v["geojson"][k].pop("name", None)
    assert a == b
    assert d["schema"] == "report/1"


def test_mapping_two_variants_in_order(syn, tmp_path):
    lighter = NetworkBundle.from_graph(_synthetic(), name="other")
    out = build_report({"zeta": syn, "alpha": lighter}, tmp_path / "r.html")
    data = embedded(out)[2]
    assert [v["name"] for v in data["variants"]] == ["zeta", "alpha"]
    assert data["title"] == "zeta"
    assert data["default_profile"] is not None


def test_graph_input(tmp_path):
    out = build_report(_synthetic(), tmp_path / "r.html")
    data = embedded(out)[2]
    assert len(data["variants"]) == 1
    assert data["variants"][0]["metrics"]["n_pipes"] == 4


def test_graph_in_mapping(tmp_path):
    data = embedded(build_report({"g": _synthetic()}, tmp_path / "r.html"))[2]
    assert data["variants"][0]["name"] == "g"


def test_empty_mapping_raises(tmp_path):
    with pytest.raises(ValueError):
        build_report({}, tmp_path / "r.html")


def test_title_and_profile_start_honoured(syn, tmp_path):
    # the first pipe-owning node other than the default
    out_ids = set(syn.pipes.from_node)
    start = sorted(out_ids - {_longest_path_start(syn)})[0]
    out = build_report(
        syn, tmp_path / "r.html", title="My <b>net</b> & co", profile_start=start
    )
    text, _, data = embedded(out)
    assert data["title"] == "My <b>net</b> & co"
    assert "<title>My &lt;b&gt;net&lt;/b&gt; &amp; co - pysewer report</title>" in text
    assert data["default_profile"] == json.loads(
        json.dumps(longitudinal_profile(syn, start).to_dict())
    )
    assert data["default_profile"] != json.loads(
        json.dumps(longitudinal_profile(syn, _longest_path_start(syn)).to_dict())
    )


def test_empty_network_builds(tmp_path):
    G = nx.DiGraph(crs=CRS("EPSG:32633"))
    G.add_node((500000, 5000000), node_type="wwtp", elevation=100.0)
    G.add_node((500100, 5000000), node_type="building", elevation=101.0)
    b = NetworkBundle.from_graph(G, name="empty")
    assert len(b.pipes) == 0
    out = build_report(b, tmp_path / "r.html")
    data = embedded(out)[2]
    assert data["default_profile"] is None
    assert data["variants"][0]["geojson"]["pipes"]["features"] == []
    assert len(data["variants"][0]["geojson"]["nodes"]["features"]) == 2


def test_script_breakout_is_escaped(syn, tmp_path):
    # a hostile node/pipe text must not close the data script
    pipes = syn.pipes.copy()
    pipes["highway"] = pipes["highway"].astype(object)
    pipes.loc[pipes.index[0], "highway"] = "</script><script>alert(1)</script><!--"
    import dataclasses

    b = dataclasses.replace(syn, pipes=pipes)
    _text, raw, data = embedded(build_report(b, tmp_path / "r.html"))
    assert "</script" not in raw and "<!--" not in raw
    hw = [
        f["properties"].get("highway")
        for f in data["variants"][0]["geojson"]["pipes"]["features"]
    ]
    assert "</script><script>alert(1)</script><!--" in hw


# --------------------------------------------------------------------- SRI
def test_sri_on_cdn_assets(syn, tmp_path):
    text = build_report(syn, tmp_path / "r.html").read_text(encoding="utf-8")
    tags = re.findall(r"<script\b[^>]*\bsrc=[^>]*>", text)
    tags += [t for t in re.findall(r"<link\b[^>]*>", text) if "stylesheet" in t]
    assert len(tags) == 3
    for t in tags:
        url = re.search(r'(?:src|href)="([^"]+)"', t).group(1)
        if url.startswith("http"):
            assert re.search(r'integrity="sha384-[A-Za-z0-9+/]+=*"', t), t
            assert 'crossorigin="anonymous"' in t, t
    for asset in CDN.values():
        assert asset["integrity"].startswith("sha384-")
        assert asset["url"] in text
    # no other external references in the file (no hidden CDN)
    hosts = set(re.findall(r'(?:src|href)="(https?://[^/"]+)', text))
    assert hosts == {"https://unpkg.com", "https://cdn.plot.ly"}


# ------------------------------------------------------------ geojson dict
def test_to_geojson_dict_rounding(real):
    r = real.to_geojson_dict()
    x = real.to_geojson_dict(rounding=False)
    json.dumps(r, allow_nan=False)
    json.dumps(x, allow_nan=False)
    assert set(r) == set(x) == {"pipes", "nodes", "metadata", "profiles"}
    lr = {
        f["properties"]["pipe_id"]: f["properties"]["length"]
        for f in r["pipes"]["features"]
    }
    lx = {
        f["properties"]["pipe_id"]: f["properties"]["length"]
        for f in x["pipes"]["features"]
    }
    exact = {
        p: float(v)
        for p, v in zip(real.pipes.pipe_id, real.pipes.length, strict=False)
        if v == v
    }
    assert lx == exact
    assert lr != exact  # real lengths have long decimals, so rounding changes them
    for p, v in lr.items():
        assert abs(v - exact[p]) < 0.05  # rounded to decimals, not mangled
        assert round(v, 2) == v or round(v, 3) == v or round(v, 1) == v


def test_to_geojson_dict_synthetic_strict(syn):
    for rounding in (True, False):
        g = syn.to_geojson_dict(rounding=rounding)
        json.dumps(g, allow_nan=False)
        assert g["metadata"]["crs_epsg"] == 4326
        assert len(g["pipes"]["features"]) == 4
        assert len(g["nodes"]["features"]) == 6


def test_to_geojson_dict_default_matches_file(syn, tmp_path):
    p = tmp_path / "n.geojson"
    syn.to_geojson(p)
    d = syn.to_geojson_dict()
    if p.is_file():
        on_disk = json.loads(p.read_text())
        if "pipes" in on_disk:
            assert on_disk["pipes"] == d["pipes"]


# --------------------------------------------------------- golden JS parity
def _numbers_close(a, b, path=""):
    if isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), path
        for k in a:
            _numbers_close(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _numbers_close(x, y, f"{path}[{i}]")
    elif isinstance(a, bool) or a is None or isinstance(a, str) or b is None:
        assert a == b, path
    elif isinstance(a, (int, float)):
        assert isinstance(b, (int, float)), path
        assert abs(a - b) <= 1e-9, f"{path}: {a} vs {b}"
    else:
        assert a == b, path


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_trace_matches_python_profile(real, tmp_path):
    data = report_data(real)
    nodes = real.nodes
    pipes_from = set(real.pipes.from_node)
    pipes_to = set(real.pipes.to_node)
    assert data["default_profile"]["start"] == _longest_path_start(real)
    picks = [_longest_path_start(real)]
    lifting = [
        n for n, f in zip(nodes.node_id, nodes.lifting_station, strict=True) if f
    ]
    if "N000023" in set(nodes.node_id):
        picks.append("N000023")
    picks += lifting[:2]
    leaves = [
        n
        for n, t in zip(nodes.node_id, nodes.node_type, strict=False)
        if t == "building" and n in pipes_from and n not in pipes_to
    ]
    picks += leaves[:2]
    mid = [n for n in sorted(pipes_from & pipes_to)][:2]
    picks += mid
    picks = list(dict.fromkeys(picks))
    assert len(picks) >= 5
    assert lifting, "test network should have a lifting station"

    dj = tmp_path / "data.json"
    dj.write_text(json.dumps(data, allow_nan=False))
    script = (
        "const fs=require('fs');"
        f"const m=require({json.dumps(str(JS))});"
        f"const d=JSON.parse(fs.readFileSync({json.dumps(str(dj))},'utf8'));"
        "const ids=JSON.parse(process.argv[1]);"
        "const out={};for(const n of ids){out[n]=m.traceProfile(d,0,n);}"
        "process.stdout.write(JSON.stringify(out));"
    )
    res = subprocess.run(
        ["node", "-e", script, json.dumps(picks)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert res.returncode == 0, res.stderr
    js = json.loads(res.stdout)
    assert set(js) == set(picks)
    for n in picks:
        py = json.loads(json.dumps(longitudinal_profile(real, n).to_dict()))
        _numbers_close(py, js[n], n)
        kinds_py = [m["kind"] for m in py["markers"]]
        assert [m["kind"] for m in js[n]["markers"]] == kinds_py
    # the lifting-station start must produce at least one marker overall
    assert any(js[n]["markers"] for n in picks)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_trace_unknown_node_raises(syn, tmp_path):
    dj = tmp_path / "d.json"
    dj.write_text(json.dumps(report_data(syn)))
    script = (
        f"const m=require({json.dumps(str(JS))});"
        f"const d=JSON.parse(require('fs').readFileSync({json.dumps(str(dj))},'utf8'));"
        "try{m.traceProfile(d,0,'NOPE');console.log('no');}"
        "catch(e){console.log('raised');}"
    )
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert res.stdout.strip() == "raised", res.stderr


# ------------------------------------------------------------ package data
@pytest.mark.parametrize("name", ["template.html", "report.js", "report.css"])
def test_package_data_found(name):
    f = resources.files("pysewer.viz.report").joinpath(name)
    assert f.is_file() and len(f.read_text(encoding="utf-8")) > 100


def test_pyproject_declares_package_data():
    txt = (REPO / "pyproject.toml").read_text()
    assert '"pysewer.viz.report"' in txt
    for ext in ("*.html", "*.js", "*.css"):
        assert ext in txt


# ------------------------------------------------------------- light import
def test_light_import():
    code = (
        "import sys, pysewer; "
        "assert 'matplotlib' not in sys.modules and 'plotly' not in sys.modules "
        "and 'osmnx' not in sys.modules, 'after pysewer'; "
        "import pysewer.viz.report; "
        "bad=[m for m in ('matplotlib','plotly','osmnx') if m in sys.modules]; "
        "assert not bad, bad"
    )
    res = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO
    )
    assert res.returncode == 0, res.stderr
