# SPDX-FileCopyrightText: 2025 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""Report comparison panel: embedded comparison, escaping, JS parity."""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pysewer.bundle import NetworkBundle
from pysewer.compare import compare
from pysewer.config.manager import reset_config
from pysewer.pipeline import Inputs, generate
from pysewer.viz.profile import longitudinal_profile
from pysewer.viz.report import _longest_path_start, build_report, report_data
from tests.test_bundle import _synthetic
from tests.test_report import JS, _numbers_close, embedded

TD = Path("tests") / "test_data"
SINK = (691350, 2553250)
REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "pysewer" / "viz" / "report"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="no node")


@pytest.fixture(autouse=True)
def _clean_config():
    reset_config()
    yield
    reset_config()


@pytest.fixture(scope="module")
def variants():
    reset_config()
    inp = Inputs(TD / "dem.tif", TD / "roads_clipped.shp", TD / "buildings_clipped.shp")
    out = {
        "default": generate(inp, sink=SINK, name="default"),
        "tmax3": generate(
            inp, sink=SINK, name="tmax3", overrides={"optimization": {"tmax": 3}}
        ),
        "lowest": generate(inp, sink="lowest", name="lowest"),
    }
    reset_config()
    return out


def _rt(obj):
    return json.loads(json.dumps(obj))


# ------------------------------------------------------------- (1) data
def test_single_variant_comparison_is_null(variants):
    d = report_data(variants["default"])
    assert d["comparison"] is None
    assert "comparison" in d  # key present, value null


def test_synthetic_single_variant_null():
    d = report_data(NetworkBundle.from_graph(_synthetic(), name="syn"))
    assert d["comparison"] is None


@pytest.mark.parametrize("keys", [("default", "tmax3"), ("default", "tmax3", "lowest")])
def test_comparison_equals_compare(variants, keys, tmp_path):
    sub = {k: variants[k] for k in keys}
    out = build_report(sub, tmp_path / "r.html")
    _, _, emb = embedded(out)
    expect = _rt(compare(sub, base=keys[0]).to_dict())
    assert emb["comparison"] == expect
    assert emb["comparison"]["base"] == keys[0]
    assert emb["comparison"]["schema"] == "compare/1"
    assert list(emb["comparison"]["names"]) == list(keys)
    assert [v["name"] for v in emb["variants"]] == list(keys)
    # in-memory data is the same as the embedded one
    assert _rt(report_data(sub)["comparison"]) == expect


def test_base_is_first_even_if_not_alphabetical(variants):
    sub = {"z_first": variants["default"], "a_second": variants["tmax3"]}
    d = report_data(sub)
    assert d["comparison"]["base"] == "z_first"


def test_variants_actually_differ(variants):
    # guard: the fixture must exercise a real diff (sink moved)
    a, b = variants["default"], variants["lowest"]
    assert list(a.pipes.from_node) != list(b.pipes.from_node) or list(
        a.pipes.to_node
    ) != list(b.pipes.to_node)


# ------------------------------------------------------------- (2) escaping
def test_hostile_variant_names_are_escaped(variants, tmp_path):
    n1 = "a</script><script>alert(1)</script>\"x'"
    n2 = 'b<!-- "q" \\ </SCRIPT>&amp;'
    sub = {n1: variants["default"], n2: variants["tmax3"]}
    out = build_report(sub, tmp_path / "r.html")
    text, raw, emb = embedded(out)
    assert "</" not in raw and "<!--" not in raw
    assert emb["comparison"]["names"] == [n1, n2]
    assert emb["comparison"]["base"] == n1
    assert [v["name"] for v in emb["variants"]] == [n1, n2]
    assert emb["comparison"] == _rt(compare(sub, base=n1).to_dict())
    # exactly one data script and no injected script element in the page
    assert text.count('id="pysewer-data"') == 1
    # the HTML parser sees only the template's own script elements
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.scripts = 0
            self.ends = 0

        def handle_starttag(self, tag, attrs):
            self.scripts += tag == "script"

        def handle_endtag(self, tag):
            self.ends += tag == "script"

    clean = build_report(
        {"a": variants["default"], "b": variants["tmax3"]}, tmp_path / "c.html"
    ).read_text(encoding="utf-8")
    p1, p2 = P(), P()
    p1.feed(text)
    p2.feed(clean)
    assert (p1.scripts, p1.ends) == (p2.scripts, p2.ends)


# ------------------------------------------------------------- (3) JS
def _run_node(script, *args):
    r = subprocess.run(
        ["node", "-e", script, *args], capture_output=True, text=True, timeout=180
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


@needs_node
def test_js_trace_each_variant_from_longest_start(variants, tmp_path):
    sub = {k: variants[k] for k in ("default", "tmax3", "lowest")}
    data = report_data(sub)
    starts = [_longest_path_start(b) for b in sub.values()]
    assert all(s is not None for s in starts)
    dj = tmp_path / "d.json"
    dj.write_text(json.dumps(data, allow_nan=False))
    script = (
        "const fs=require('fs');"
        f"const m=require({json.dumps(str(JS))});"
        f"const d=JSON.parse(fs.readFileSync({json.dumps(str(dj))},'utf8'));"
        "const st=JSON.parse(process.argv[1]);"
        "process.stdout.write(JSON.stringify(st.map((s,i)=>m.traceProfile(d,i,s))));"
    )
    js = json.loads(_run_node(script, json.dumps(starts)))
    for (name, b), s, tr in zip(sub.items(), starts, js, strict=True):
        py = _rt(longitudinal_profile(b, s).to_dict())
        _numbers_close(py, tr, name)
        assert [m["kind"] for m in tr["markers"]] == [m["kind"] for m in py["markers"]]


def _js_longest_starts(data_path):
    """Evaluate longestStart/fnum extracted from report.js against data."""
    src = (PKG / "report.js").read_text(encoding="utf-8")
    m = re.search(r"\n  function longestStart\(variant\) \{.*?\n  \}\n", src, re.S)
    f = re.search(r"\n  function fnum\(v\) \{.*?\n  \}\n", src, re.S)
    assert m and f, "longestStart/fnum not found"
    script = (
        "const fs=require('fs');"
        f"const D=JSON.parse(fs.readFileSync({json.dumps(str(data_path))},'utf8'));"
        "const VARIANTS=D.variants;const DEFAULT_PROFILE=D.default_profile;"
        + f.group(0)
        + m.group(0)
        + "process.stdout.write(JSON.stringify(VARIANTS.map(longestStart)));"
    )
    return json.loads(_run_node(script))


@needs_node
def test_js_longest_start_matches_python(variants, tmp_path):
    sub = {k: variants[k] for k in ("default", "tmax3", "lowest")}
    data = report_data(sub)
    dj = tmp_path / "d.json"
    dj.write_text(json.dumps(data, allow_nan=False))
    got = _js_longest_starts(dj)
    assert got == [_longest_path_start(b) for b in sub.values()]


@needs_node
def test_js_longest_start_empty_and_synthetic(tmp_path):
    import networkx as nx
    from pyproj import CRS

    G = nx.DiGraph(crs=CRS("EPSG:32633"))
    G.add_node((500000, 5000000), node_type="wwtp", elevation=100.0)
    G.add_node((500100, 5000000), node_type="building", elevation=101.0)
    sub = {
        "empty": NetworkBundle.from_graph(G, name="empty"),
        "syn": NetworkBundle.from_graph(_synthetic(), name="syn"),
    }
    assert sub["syn"].crs is not None
    data = report_data(sub)
    assert data["default_profile"] is None
    dj = tmp_path / "d.json"
    dj.write_text(json.dumps(data, allow_nan=False))
    got = _js_longest_starts(dj)
    assert got[0] is None
    assert got[1] == _longest_path_start(sub["syn"])


@needs_node
def test_js_exports_unchanged_pure_section():
    out = _run_node(
        f"const m=require({json.dumps(str(JS))});"
        "process.stdout.write(JSON.stringify(Object.keys(m)));"
    )
    assert json.loads(out) == ["traceProfile"]


def test_pure_section_has_no_dom_or_comparison_refs():
    src = (PKG / "report.js").read_text(encoding="utf-8")
    pure = src.split("END PURE TRACE")[0]
    for tok in ("document", "window", "COMPARISON", "diffOn", "Plotly", "maplibregl"):
        assert tok not in pure, tok


# ------------------------------------------------------------- (4) template
def test_template_has_compare_elements_hidden_by_default():
    t = (PKG / "template.html").read_text(encoding="utf-8")
    for i in ("variant-switch", "diff-bar", "compare-section"):
        tag = re.search(rf"<[^>]*id=\"{i}\"[^>]*>", t)
        assert tag, i
        assert re.search(r"\bhidden\b", tag.group(0)), f"{i} not hidden by default"
    for i in ("variant-select", "compare-select", "diff-toggle"):
        assert f'id="{i}"' in t


def test_js_reveals_compare_elements_only_when_multi():
    js = (PKG / "report.js").read_text(encoding="utf-8")
    assert "const MULTI = VARIANTS.length >= 2 && !!COMPARISON;" in js
    body = re.search(r"function initVariants\(\) \{(.*?)\n  \}\n", js, re.S).group(1)
    assert body.lstrip().startswith("if (!MULTI) return;")
    for i in ("variant-switch", "diff-bar", "compare-section"):
        assert f'$("{i}").hidden = false' in body
        # nothing outside initVariants un-hides them
        assert js.count(f'$("{i}").hidden = false') == 1
    for fn in ("setVariant", "renderCompare"):
        assert re.search(rf"function {fn}\([^)]*\) \{{\n(?:.*\n)??.*!MULTI", js)
    for key in ("setVariant", "setDiff", "longestStart", "current", "compareName"):
        assert re.search(rf"\b{key}\b", js.split("window.pysewerReport")[1])


def test_single_variant_page_data_drives_hidden(variants, tmp_path):
    out = build_report(variants["default"], tmp_path / "r.html")
    text, _, emb = embedded(out)
    assert emb["comparison"] is None and len(emb["variants"]) == 1
    for i in ("variant-switch", "diff-bar", "compare-section"):
        tag = re.search(rf"<[^>]*id=\"{i}\"[^>]*>", text)
        assert tag and "hidden" in tag.group(0)


# ------------------------------------------------------------- (5) size
def test_report_size_growth_reasonable(variants, tmp_path):
    one = build_report(variants["default"], tmp_path / "one.html").stat().st_size
    three = (
        build_report(
            {k: variants[k] for k in ("default", "tmax3", "lowest")},
            tmp_path / "t.html",
        )
        .stat()
        .st_size
    )
    two = (
        build_report(
            {k: variants[k] for k in ("default", "tmax3")}, tmp_path / "w.html"
        )
        .stat()
        .st_size
    )
    assert one < two < three
    # per added variant: geojson+metrics (~1x) plus comparison diff (<~1x)
    assert two < 3.0 * one
    assert three < 4.5 * one
    # static assets (js+css+template) stay small
    assets = sum(
        (PKG / n).stat().st_size for n in ("report.js", "report.css", "template.html")
    )
    assert assets < 150_000


# ------------------------------------------------------------- (6) light import
def test_import_pysewer_still_light():
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, pysewer; "
            "bad=[m for m in ('matplotlib','osmnx') if m in sys.modules]; "
            "assert not bad, bad",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stderr
