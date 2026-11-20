# SPDX-FileCopyrightText: 2025 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

"""
Self-contained HTML report of a designed sewer network.

:func:`build_report` writes one HTML file. The data (GeoJSON in WGS84, metrics,
quality findings, one default profile) is inlined as JSON. MapLibre GL JS and
Plotly.js load from a CDN with SRI hashes; the basemap is OpenFreeMap. The
Python side uses the standard library only (``string.Template``, ``json``,
``importlib.resources``).

The JavaScript traces the flow path to the sink itself, so every node can be
clicked. For the default start node the trace must equal
``Profile.to_dict()`` (embedded as ``default_profile``). The GeoJSON is
embedded unrounded (coordinates keep 7 decimals), so the trace equals the
embedded profile to about 1e-6.
"""

from __future__ import annotations

import json
import string
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any

from pysewer.bundle import NetworkBundle
from pysewer.metrics import network_metrics, quality_checks, summarize_findings
from pysewer.viz.profile import longitudinal_profile

__all__ = ["CDN", "build_report", "report_data"]

#: Pinned CDN assets (version, URL, SRI sha384).
CDN = {
    "maplibre_js": {
        "url": "https://unpkg.com/maplibre-gl@5.6.0/dist/maplibre-gl.js",
        "integrity": "sha384-GfxBM9x46BaAFxtCq39Fxir8fNZ4VDnwgfi6Kzi5/F1tAFsm0amuuV8kd+Pxzuf/",
    },
    "maplibre_css": {
        "url": "https://unpkg.com/maplibre-gl@5.6.0/dist/maplibre-gl.css",
        "integrity": "sha384-Nq6PQ+9vJPvw7U/VfDELyrWoGQMsy0gi6QShhaSrGzkpF5KkM40csg2leky+YMTd",
    },
    "plotly_js": {
        "url": "https://cdn.plot.ly/plotly-basic-2.35.2.min.js",
        "integrity": "sha384-wQ3lfCxuvLfhHGiSdHF5+e3NZ1zNwEMd8/nII+K7VKChF9llO/OwOcn/aVRkB7az",
    },
}


def _text(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text(encoding="utf-8")


def _as_bundle(obj: Any, name: str) -> NetworkBundle:
    if isinstance(obj, NetworkBundle):
        return obj
    return NetworkBundle.from_graph(obj, name=name)


def _normalise(bundles: Any) -> dict[str, NetworkBundle]:
    if isinstance(bundles, Mapping):
        if not bundles:
            raise ValueError("bundles is empty")
        return {str(k): _as_bundle(v, str(k)) for k, v in bundles.items()}
    b = _as_bundle(bundles, "network")
    return {b.name: b}


def _longest_path_start(bundle: NetworkBundle) -> str | None:
    """Return the node with the longest pipe-length path to its sink."""
    pipes = bundle.pipes
    out: dict[str, tuple[str, float]] = {}
    for u, v, ln in zip(
        pipes["from_node"], pipes["to_node"], pipes["length"], strict=True
    ):
        out.setdefault(str(u), (str(v), float(ln) if ln == ln else 0.0))
    memo: dict[str, float] = {}

    def dist(n: str) -> float:
        if n in memo:
            return memo[n]
        total, cur, seen = 0.0, n, {n}
        while cur in out:
            nxt, ln = out[cur]
            if nxt in seen:
                break
            total += ln
            seen.add(nxt)
            cur = nxt
        memo[n] = total
        return total

    best, best_d = None, 0.0
    for n in sorted(out):
        d = dist(n)
        if d > best_d:
            best, best_d = n, d
    return best


def _variant(name: str, bundle: NetworkBundle, findings_config: Any) -> dict:
    findings = quality_checks(bundle, findings_config)
    summary = summarize_findings(findings, findings_config, bundle=bundle)
    geo = bundle.to_geojson_dict(rounding=False)
    # Projected CRS (EPSG) of the source tables, for the header.
    epsg = bundle.crs.to_epsg() if bundle.crs is not None else None
    geo["metadata"].setdefault("source_crs_epsg", epsg)
    area = None
    if bundle.crs is not None and bundle.crs.area_of_use is not None:
        # (west, south, east, north) in WGS84, used to frame an empty network
        area = [round(float(v), 4) for v in bundle.crs.area_of_use.bounds]
    return {
        "name": name,
        "geojson": geo,
        "crs_area": area,
        "metrics": network_metrics(bundle),
        "findings": json.loads(findings.to_json(orient="records")),
        "summary": summary,
    }


def report_data(
    bundles: Any,
    *,
    title: str | None = None,
    profile_start: str | None = None,
    findings_config: Any = "bundle",
) -> dict:
    """
    Return the JSON-able data object that :func:`build_report` embeds.

    Keys: ``schema``, ``title``, ``variants`` (list, one dict per variant with
    ``name``, ``geojson``, ``crs_area``, ``metrics``, ``findings``,
    ``summary``) and
    ``default_profile`` (``Profile.to_dict()`` for the first variant, or None
    if it has no pipes).
    """
    named = _normalise(bundles)
    variants = [_variant(n, b, findings_config) for n, b in named.items()]
    first_name, first = next(iter(named.items()))
    start = profile_start or _longest_path_start(first)
    profile = None
    if start is not None:
        profile = longitudinal_profile(first, start).to_dict()
    return {
        "schema": "report/1",
        "title": title or first_name,
        "variants": variants,
        "default_profile": profile,
    }


def _embed(obj: Any) -> str:
    """Serialise to JSON that is safe inside a ``<script>`` element."""
    text = json.dumps(obj, allow_nan=False, separators=(",", ":"), ensure_ascii=True)
    # "<\/" and "\u003c" are valid JSON escapes; "<\!" is not.
    return text.replace("</", "<\\/").replace("<!--", "\\u003c!--")


def build_report(
    bundles: Any,
    out_html: str | Path,
    *,
    title: str | None = None,
    profile_start: str | None = None,
    findings_config: Any = "bundle",
) -> Path:
    """
    Write a self-contained HTML report.

    Parameters
    ----------
    bundles : NetworkBundle, networkx.DiGraph or Mapping
        One network or ``{variant name: network}``. The first variant is
        shown.
    out_html : str or Path
        Output file.
    title : str, optional
        Report title; default the name of the first variant.
    profile_start : str, optional
        ``node_id`` where the default profile starts; default the node with
        the longest flow path to its sink.
    findings_config : None, "bundle", Config or dict
        Thresholds for :func:`pysewer.metrics.quality_checks`.

    Returns
    -------
    pathlib.Path
        The written file.
    """
    data = report_data(
        bundles,
        title=title,
        profile_start=profile_start,
        findings_config=findings_config,
    )
    html = string.Template(_text("template.html")).substitute(
        title=_html_escape(data["title"]),
        css=_text("report.css"),
        js=_text("report.js").replace("</script", "<\\/script"),
        data=_embed(data),
        maplibre_css_url=CDN["maplibre_css"]["url"],
        maplibre_css_sri=CDN["maplibre_css"]["integrity"],
        maplibre_js_url=CDN["maplibre_js"]["url"],
        maplibre_js_sri=CDN["maplibre_js"]["integrity"],
        plotly_js_url=CDN["plotly_js"]["url"],
        plotly_js_sri=CDN["plotly_js"]["integrity"],
    )
    out = Path(out_html)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def _html_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
