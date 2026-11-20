// SPDX-FileCopyrightText: 2025 Helmholtz Centre for Environmental Research (UFZ)
// SPDX-License-Identifier: GPL-3.0-only
//
// pysewer report: map, profile, metrics and quality table.
// Vanilla ES2020, no build step. Data comes from the inlined JSON element
// "pysewer-data". All text from the data is set with textContent.
//
// traceProfile() reproduces pysewer.viz.profile.longitudinal_profile().to_dict().
// The GeoJSON is embedded unrounded, so the trace equals the embedded
// default_profile to about 1e-6. The pure trace section runs in Node:
// require("report.js").traceProfile(data, variantIndex, nodeId).
(function () {
  "use strict";

  function fnum(v) {
    if (v === null || v === undefined || v === "") return NaN;
    const x = Number(v);
    return Number.isFinite(x) ? x : NaN;
  }

  function out(v) {
    return Number.isFinite(v) ? v : null;
  }

  // ======================= PURE TRACE (no DOM, no libraries) =======================
  const indexCache = new WeakMap();

  function getVariant(data, ref) {
    const variants = data.variants;
    if (ref === undefined || ref === null) return variants[0];
    if (typeof ref === "number") return variants[ref];
    return variants.find((v) => v.name === ref) || variants[0];
  }

  function buildIndex(variant) {
    let idx = indexCache.get(variant);
    if (idx) return idx;
    const pipes = variant.geojson.pipes.features;
    const nodes = variant.geojson.nodes.features;
    idx = {
      pipeById: new Map(),
      nodeById: new Map(),
      outByFrom: new Map(),
      inByTo: new Map(),
    };
    for (const f of pipes) {
      idx.pipeById.set(f.properties.pipe_id, f);
      const u = f.properties.from_node;
      if (!idx.outByFrom.has(u)) idx.outByFrom.set(u, []);
      idx.outByFrom.get(u).push(f);
      const v = f.properties.to_node;
      if (!idx.inByTo.has(v)) idx.inByTo.set(v, []);
      idx.inByTo.get(v).push(f);
    }
    for (const f of nodes) idx.nodeById.set(f.properties.node_id, f);
    indexCache.set(variant, idx);
    return idx;
  }

  // Display invert of a pressurised pipe: ground - depth(s), depth interpolated
  // between the end depths of the stored invert (rule from params).
  function pressurizedInvert(ground, inv, c0, tmin) {
    let depthUp = ground[0][1] - inv[0][1];
    let depthDown = ground[ground.length - 1][1] - inv[inv.length - 1][1];
    if (!Number.isFinite(depthUp)) depthUp = tmin;
    if (!Number.isFinite(depthDown)) depthDown = tmin;
    if (!(Number.isFinite(depthUp) && Number.isFinite(depthDown))) return inv;
    const length = ground[ground.length - 1][0] - c0;
    const res = [];
    for (const p of ground) {
      if (!Number.isFinite(p[1])) continue;
      const frac = length > 0 ? (p[0] - c0) / length : 0;
      res.push([p[0], p[1] - (depthUp + (depthDown - depthUp) * frac)]);
    }
    return res;
  }

  function pairs(list, c0) {
    return (list || []).map((p) => [c0 + fnum(p[0]), fnum(p[1])]);
  }

  function outPts(list) {
    return list.map((p) => [out(p[0]), out(p[1])]);
  }

  /**
   * Trace the flow path from nodeId to the sink and return an object shaped
   * like Profile.to_dict() (schema profile/1).
   *
   * data is the parsed report JSON; marker rules, units and schema come from
   * data.default_profile (params, units, schema).
   */
  function traceProfile(data, variantRef, nodeId) {
    const dp = data.default_profile || null;
    const PARAMS = (dp && dp.params) || { drop_threshold: 0.05, lift_tol: 1e-6 };
    const UNITS = (dp && dp.units) || {
      chainage: "m", elevation: "m", diameter: "m",
      peak_flow: "m3/s", velocity: "m/s", d_over_D: "1",
    };
    const SCHEMA = (dp && dp.schema) || "profile/1";
    const variant = getVariant(data, variantRef);
    const idx = buildIndex(variant);
    const profiles = variant.geojson.profiles || {};
    const meta = variant.geojson.metadata || {};
    const start = String(nodeId);
    if (!idx.nodeById.has(start)) {
      throw new Error(`start node ${JSON.stringify(start)} is not in the bundle`);
    }

    const pathNodes = [start];
    const pathRows = [];
    const seen = new Set([start]);
    let cur = start;
    while (idx.outByFrom.has(cur)) {
      const rows = idx.outByFrom.get(cur);
      if (rows.length > 1) {
        throw new Error(
          `node ${cur} has ${rows.length} outgoing pipes; the network is not a tree`
        );
      }
      const row = rows[0].properties;
      const nxt = String(row.to_node);
      if (seen.has(nxt)) throw new Error(`cycle detected at node ${nxt}`);
      seen.add(nxt);
      pathRows.push(row);
      pathNodes.push(nxt);
      cur = nxt;
    }
    const end = cur;

    const segments = [];
    let c0 = 0;
    for (const row of pathRows) {
      const pid = String(row.pipe_id);
      const length = fnum(row.length);
      const diameter = fnum(row.diameter);
      const ptype = String(row.pipe_type);
      const prof = profiles[pid];
      let ground;
      let inv;
      if (prof && Object.keys(prof).length > 0) {
        ground = pairs(prof.ground, c0);
        inv = pairs(prof.invert, c0);
      } else {
        ground = [
          [c0, fnum(row.ground_up)],
          [c0 + length, fnum(row.ground_down)],
        ].filter((p) => Number.isFinite(p[1]));
        inv = [
          [c0, fnum(row.invert_up)],
          [c0 + length, fnum(row.invert_down)],
        ].filter((p) => Number.isFinite(p[1]));
      }
      if (
        ptype === "pressurized" &&
        ground.length >= 2 &&
        inv.length > 0 &&
        PARAMS.pressurized_invert === "ground_minus_interpolated_end_depth"
      ) {
        inv = pressurizedInvert(ground, inv, c0, fnum(PARAMS.tmin));
      }
      const crown = inv.map((p) => [p[0], p[1] + diameter]);
      const dOverD = fnum(row.d_over_D);
      let water = [];
      if (ptype === "gravity" && Number.isFinite(dOverD) && Number.isFinite(diameter)) {
        water = inv.map((p) => [p[0], p[1] + dOverD * diameter]);
      }
      segments.push({
        pipe_id: pid,
        from_node: String(row.from_node),
        to_node: String(row.to_node),
        pipe_type: ptype,
        diameter: out(diameter),
        chainage_start: out(c0),
        chainage_end: out(c0 + length),
        ground: outPts(ground),
        invert: outPts(inv),
        crown: outPts(crown),
        peak_flow: out(fnum(row.peak_flow)),
        velocity: out(fnum(row.velocity)),
        d_over_D: out(dOverD),
        water: outPts(water),
        // internal numeric copies keep NaN-safe arithmetic below
        _inv: inv,
        _end: c0 + length,
      });
      c0 += length;
    }

    const markers = [];
    pathNodes.forEach((nid, i) => {
      const node = idx.nodeById.get(nid).properties;
      const inc = i > 0 ? segments[i - 1] : null;
      const outg = i < segments.length ? segments[i] : null;
      const invIn = inc && inc._inv.length ? inc._inv[inc._inv.length - 1][1] : NaN;
      const invOut = outg && outg._inv.length ? outg._inv[0][1] : NaN;
      let kind;
      if (i === 0) {
        kind = "start";
      } else if (outg === null) {
        kind = "sink";
      } else {
        kind = node.pumping_station
          ? "pumping_station"
          : node.lifting_station
          ? "lifting_station"
          : "manhole";
        const finite = Number.isFinite(invIn) && Number.isFinite(invOut);
        if (kind === "manhole" && finite && invIn - invOut >= PARAMS.drop_threshold) {
          kind = "drop";
        } else if (
          kind === "pumping_station" &&
          finite &&
          inc.pipe_type === "pressurized" &&
          Math.abs(invOut - invIn) <= PARAMS.lift_tol
        ) {
          kind = "pump_relay";
        }
      }
      markers.push({
        node_id: nid,
        chainage: out(inc ? inc._end : 0),
        role: String(node.role),
        ground: out(fnum(node.ground)),
        invert_in: out(invIn),
        invert_out: out(invOut),
        kind: kind,
      });
    });

    for (const s of segments) {
      delete s._inv;
      delete s._end;
    }
    return {
      schema: SCHEMA,
      units: Object.assign({}, UNITS),
      start: start,
      sink: end,
      crs_wkt: String(meta.source_crs_wkt || meta.crs_wkt || ""),
      params: Object.assign({}, PARAMS),
      total_length: out(c0),
      node_ids: pathNodes,
      pipe_ids: segments.map((s) => s.pipe_id),
      segments: segments,
      markers: markers,
    };
  }

  // ======================= END PURE TRACE =======================

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { traceProfile: traceProfile };
  }
  if (typeof document === "undefined") return;

  // ------------------------------------------------------------ data
  const DATA = JSON.parse(document.getElementById("pysewer-data").textContent);
  const VARIANTS = DATA.variants;
  const DEFAULT_PROFILE = DATA.default_profile || null;
  const ICON_ARROW = "pysewer-flow-arrow";
  const ICON_STAR = "pysewer-sink-star";
  const PARAMS = (DEFAULT_PROFILE && DEFAULT_PROFILE.params) || {
    drop_threshold: 0.05,
    lift_tol: 1e-6,
  };
  let current = VARIANTS[0];

  // ------------------------------------------------------------ helpers
  const $ = (id) => document.getElementById(id);

  function el(tag, props, ...kids) {
    const n = document.createElement(tag);
    if (props) {
      for (const [k, v] of Object.entries(props)) {
        if (k === "class") n.className = v;
        else if (k === "text") n.textContent = v;
        else n.setAttribute(k, v);
      }
    }
    for (const kid of kids) {
      if (kid == null) continue;
      n.append(kid);
    }
    return n;
  }

  function fmt(v, digits) {
    const x = fnum(v);
    return Number.isFinite(x) ? x.toFixed(digits) : "\u2013";
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function toRgba(hex, alpha) {
    const m = /^#([0-9a-f]{6})$/i.exec(hex);
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
  }

  function effectiveTheme() {
    const t = document.documentElement.getAttribute("data-theme");
    if (t === "light" || t === "dark") return t;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }


  // ------------------------------------------------------------ header, metrics, banner
  const SEV_ORDER = { error: 0, warning: 1, info: 2 };

  function crsLabel(meta) {
    if (meta.source_crs_epsg) return "EPSG:" + meta.source_crs_epsg;
    const m = /^(?:PROJCRS|GEOGCRS)\["([^"]+)"/.exec(meta.source_crs_wkt || "");
    return m ? m[1] : "unknown";
  }

  function renderHeader() {
    const meta = current.geojson.metadata || {};
    const m = current.metrics;
    document.title = `${DATA.title} - pysewer report`;
    $("title").textContent = DATA.title;
    $("subtitle").textContent =
      `Variant: ${current.name}. ${m.n_pipes} pipes, ${m.n_nodes} nodes, ` +
      `${m.buildings_total} buildings.`;
    const prov = meta.provenance || {};
    const dl = $("meta");
    dl.textContent = "";
    const rows = [
      ["pysewer", meta.pysewer_version || "unknown"],
      ["CRS of the design", crsLabel(meta)],
      ["Created", meta.created_at ? String(meta.created_at).replace("T", " ").slice(0, 16) + " UTC" : "unknown"],
      ["DEM source", prov.dem_source || "unknown"],
    ];
    for (const [k, v] of rows) {
      dl.append(el("dt", { text: k }), el("dd", { text: String(v) }));
    }
    const known = prov.dem_source ? String(prov.dem_source) : "";
    const note = $("dem-note");
    if (/copernicus|nasadem/i.test(known)) {
      note.hidden = false;
      note.textContent =
        `Elevations come from ${known}; a 30 m DSM includes buildings and trees, ` +
        "so ground levels can be too high next to buildings and under tree cover.";
    } else if (!known || /^unknown/i.test(known)) {
      note.hidden = false;
      note.textContent =
        "The elevation source is not recorded. If it is a 30 m surface model (DSM), " +
        "ground levels can be too high where there are buildings and trees.";
    }
  }

  function renderMetrics() {
    const m = current.metrics;
    const items = [
      ["Total pipe length", fmt(m.total_length_m, 0), "m"],
      ["Gravity length", fmt(m.gravity_length_m, 0), "m"],
      ["Pressure main length", fmt(m.pressurized_length_m, 0), "m"],
      ["Pumping stations", String(m.n_pumping_stations), ""],
      ["Lifting stations", String(m.n_lifting_stations), ""],
      ["Private pumps", String(m.n_private_pumps), ""],
      ["Max trench depth (gravity)", fmt(m.max_depth_m, 2), "m"],
      ["Mean trench depth (gravity)", fmt(m.mean_depth_m, 2), "m"],
      ["Max filling ratio d/D", fmt(m.max_d_over_D_gravity, 2), ""],
      ["Velocity min / max", `${fmt(m.velocity_min_ms, 2)} / ${fmt(m.velocity_max_ms, 2)}`, "m/s"],
      ["Longest flow path", fmt(m.longest_flow_path_m, 0), "m"],
      ["Buildings connected", `${m.buildings_connected} / ${m.buildings_total}`, ""],
    ];
    const dl = $("metrics");
    dl.textContent = "";
    for (const [k, v, u] of items) {
      const dd = el("dd", { text: v });
      if (u) dd.append(" ", el("span", { class: "unit", text: u }));
      dl.append(el("div", { class: "metric" }, el("dt", { text: k }), dd));
    }
  }

  function renderBanner() {
    const box = $("banner");
    box.textContent = "";
    const m = current.metrics;
    if (m.n_pipes === 0) {
      box.append(el("div", { class: "banner-line info" },
        el("span", { class: "tag", text: "Info" }),
        "Nothing to check: the network has no pipes."));
      return;
    }
    const summary = current.summary || [];
    if (summary.length === 0) {
      box.append(
        el("div", { class: "banner-line ok" },
          el("span", { class: "tag", text: "OK" }),
          "No problems found.")
      );
      return;
    }
    const line = (s) =>
      el("div", { class: `banner-line ${s.severity}` },
        el("span", { class: "tag", text: s.severity }),
        s.message);
    // errors first (the summary is already sorted); show the top two
    summary.slice(0, 2).forEach((s) => box.append(line(s)));
    if (summary.length > 2) {
      const det = el("details", { class: "banner-more" },
        el("summary", { text: `Show all ${summary.length} summary lines` }));
      const wrap = el("div", { class: "banner" });
      summary.slice(2).forEach((s) => wrap.append(line(s)));
      det.append(wrap);
      box.append(det);
    }
  }

  // ------------------------------------------------------------ map
  const NODE_PALETTE = {
    light: {
      sink: "#1a7f37", building: "#6b7280", stroke: "#ffffff",
      manhole: "#ffffff", manholeStroke: "#4b5563", undesigned: "#6b7280",
      arrow: "#ffffff", dn: ["#6cc24a", "#1fa187", "#277f8e", "#46327e", "#1b0a33"],
    },
    dark: {
      sink: "#56d364", building: "#aab4c0", stroke: "#0e1318",
      manhole: "#18202a", manholeStroke: "#aab4c0", undesigned: "#aab4c0",
      arrow: "#0b1220", dn: ["#fde725", "#a0da39", "#4ac16d", "#1fa187", "#4a9be0"],
    },
  };
  const DN_BREAKS = [0.25, 0.35, 0.45, 0.7];
  const DN_LABELS = ["DN 200 or less", "DN 300", "DN 400", "DN 500 to 600", "DN 800 or more"];

  let map = null;
  let mapReady = false;
  let styleLoaded = false;
  let fallbackShown = false;
  let tileMsgShown = false;
  let popup = null;
  let pathFC = { type: "FeatureCollection", features: [] };
  let selFC = { type: "FeatureCollection", features: [] };
  let selectedStart = null;

  const PIPE_LAYERS = ["pipes-gravity", "pipes-pressure", "pipes-undesigned"];

  function emptyStyle() {
    return {
      version: 8,
      sources: {},
      layers: [
        { id: "bg", type: "background", paint: { "background-color": cssVar("--bg") || "#f4f6f8" } },
      ],
    };
  }

  function styleUrl() {
    return effectiveTheme() === "dark"
      ? "https://tiles.openfreemap.org/styles/dark"
      : "https://tiles.openfreemap.org/styles/positron";
  }

  function allBounds() {
    const b = [Infinity, Infinity, -Infinity, -Infinity];
    const add = (c) => {
      if (c[0] < b[0]) b[0] = c[0];
      if (c[1] < b[1]) b[1] = c[1];
      if (c[0] > b[2]) b[2] = c[0];
      if (c[1] > b[3]) b[3] = c[1];
    };
    for (const f of current.geojson.pipes.features) f.geometry.coordinates.forEach(add);
    for (const f of current.geojson.nodes.features) add(f.geometry.coordinates);
    return Number.isFinite(b[0]) ? [[b[0], b[1]], [b[2], b[3]]] : null;
  }

  function areaBounds() {
    const a = current.crs_area;
    return a && a.length === 4 ? [[a[0], a[1]], [a[2], a[3]]] : null;
  }

  function coordsBounds(coords) {
    const b = [Infinity, Infinity, -Infinity, -Infinity];
    for (const c of coords) {
      b[0] = Math.min(b[0], c[0]); b[1] = Math.min(b[1], c[1]);
      b[2] = Math.max(b[2], c[0]); b[3] = Math.max(b[3], c[1]);
    }
    return [[b[0], b[1]], [b[2], b[3]]];
  }

  // Arrow colours: dark fill with a white outline on the light theme, white fill
  // with a dark outline on the dark theme (readable on every DN colour).
  const ARROW_COLORS = {
    light: { fill: "#1f2937", edge: "#ffffff" },
    dark: { fill: "#ffffff", edge: "#0b1220" },
  };

  function arrowImage() {
    const s = 32;
    const c = document.createElement("canvas");
    c.width = s; c.height = s;
    const g = c.getContext("2d");
    const col = ARROW_COLORS[effectiveTheme()];
    g.beginPath();
    g.moveTo(5, 5); g.lineTo(28, 16); g.lineTo(5, 27); g.lineTo(10, 16); g.closePath();
    g.lineWidth = 4;
    g.lineJoin = "round";
    g.strokeStyle = col.edge;
    g.stroke();
    g.fillStyle = col.fill;
    g.fill();
    return g.getImageData(0, 0, s, s);
  }

  function starImage() {
    const s = 40;
    const c = document.createElement("canvas");
    c.width = s; c.height = s;
    const g = c.getContext("2d");
    g.beginPath();
    for (let i = 0; i < 10; i++) {
      const r = i % 2 === 0 ? 17 : 7.5;
      const a = -Math.PI / 2 + (i * Math.PI) / 5;
      g.lineTo(20 + r * Math.cos(a), 20 + r * Math.sin(a));
    }
    g.closePath();
    g.fillStyle = NODE_PALETTE[effectiveTheme()].sink;
    g.fill();
    g.lineWidth = 3;
    g.strokeStyle = effectiveTheme() === "dark" ? "#0e1318" : "#ffffff";
    g.stroke();
    return g.getImageData(0, 0, s, s);
  }

  function widthExpr(scale) {
    const w = ["+", 1.5, ["*", ["to-number", ["get", "diameter"], 0.2], 14]];
    return [
      "interpolate", ["linear"], ["zoom"],
      13, ["*", w, 0.5 * scale],
      19, ["*", w, 1.7 * scale],
    ];
  }

  // Add images, sources and layers of the network. Safe to call again: it does
  // nothing while the network source is present and re-adds everything after a
  // style change (setStyle drops custom sources and layers).
  function setupLayers() {
    if (map.getSource("pipes")) return;
    const pal = NODE_PALETTE[effectiveTheme()];
    const gj = current.geojson;
    // Own names: the basemap sprite has its own "arrow" and "star" icons,
    // which replace ours when the style reloads.
    for (const name of [ICON_ARROW, ICON_STAR]) {
      if (map.hasImage(name)) map.removeImage(name);
    }
    map.addImage(ICON_ARROW, arrowImage());
    map.addImage(ICON_STAR, starImage());

    map.addSource("pipes", { type: "geojson", data: gj.pipes });
    map.addSource("nodes", { type: "geojson", data: gj.nodes });
    map.addSource("path", { type: "geojson", data: pathFC });
    map.addSource("sel", { type: "geojson", data: selFC });

    const isType = (t) => ["==", ["get", "pipe_type"], t];
    const dnColor = ["step", ["to-number", ["get", "diameter"], 0.2], pal.dn[0]];
    DN_BREAKS.forEach((b, i) => dnColor.push(b, pal.dn[i + 1]));

    map.addLayer({
      id: "path-glow", type: "line", source: "path",
      filter: ["==", ["geometry-type"], "LineString"],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": cssVar("--highlight"), "line-width": widthExpr(1.0).map((x, i) => i === 4 || i === 6 ? ["+", 6, x] : x), "line-opacity": 0.7 },
    });
    map.addLayer({
      id: "pipes-undesigned", type: "line", source: "pipes", filter: isType("undesigned"),
      layout: { "line-cap": "round" },
      paint: { "line-color": pal.undesigned, "line-width": 2, "line-dasharray": [0.5, 2.5] },
    });
    map.addLayer({
      id: "pipes-gravity", type: "line", source: "pipes", filter: isType("gravity"),
      layout: { "line-cap": "butt", "line-join": "round" },
      paint: { "line-color": dnColor, "line-width": widthExpr(1.0) },
    });
    map.addLayer({
      id: "pipes-pressure", type: "line", source: "pipes", filter: isType("pressurized"),
      layout: { "line-join": "round" },
      paint: { "line-color": cssVar("--pressure"), "line-width": widthExpr(1.0), "line-dasharray": [2, 1.5] },
    });
    map.addLayer({
      id: "arrows", type: "symbol", source: "pipes", minzoom: 14.5,
      filter: ["in", ["get", "pipe_type"], ["literal", ["gravity", "pressurized"]]],
      layout: {
        "symbol-placement": "line", "symbol-spacing": 55, "icon-image": ICON_ARROW,
        "icon-size": ["interpolate", ["linear"], ["zoom"], 14.5, 0.65, 19, 1.2],
        "icon-allow-overlap": true, "icon-ignore-placement": true,
        "icon-rotation-alignment": "map",
      },
    });

    const nodeFilter = (role) => ["==", ["get", "role"], role];
    map.addLayer({
      id: "nodes-manhole", type: "circle", source: "nodes", minzoom: 15.5,
      filter: nodeFilter("junction"),
      paint: { "circle-radius": 3, "circle-color": pal.manhole, "circle-stroke-color": pal.manholeStroke, "circle-stroke-width": 1.2 },
    });
    map.addLayer({
      id: "nodes-building", type: "circle", source: "nodes",
      filter: ["all", nodeFilter("building"), ["!=", ["get", "private_pump"], true]],
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 1.5, 19, 4], "circle-color": pal.building, "circle-stroke-color": pal.stroke, "circle-stroke-width": 0.8 },
    });
    map.addLayer({
      id: "nodes-private", type: "circle", source: "nodes",
      filter: ["==", ["get", "private_pump"], true],
      paint: { "circle-radius": 5, "circle-color": pal.building, "circle-stroke-color": cssVar("--pressure"), "circle-stroke-width": 3 },
    });
    map.addLayer({
      id: "nodes-lifting", type: "circle", source: "nodes", filter: nodeFilter("lifting_station"),
      paint: { "circle-radius": 8, "circle-color": cssVar("--lift"), "circle-stroke-color": pal.stroke, "circle-stroke-width": 2 },
    });
    map.addLayer({
      id: "nodes-pumping", type: "circle", source: "nodes",
      filter: ["all", nodeFilter("pumping_station"), ["!=", ["get", "private_pump"], true]],
      paint: { "circle-radius": 8, "circle-color": cssVar("--pressure"), "circle-stroke-color": pal.stroke, "circle-stroke-width": 2 },
    });
    map.addLayer({
      id: "nodes-sink", type: "symbol", source: "nodes", filter: nodeFilter("sink"),
      layout: { "icon-image": ICON_STAR, "icon-size": 0.8, "icon-allow-overlap": true },
    });
    map.addLayer({
      id: "path-start", type: "circle", source: "path",
      filter: ["==", ["geometry-type"], "Point"],
      paint: { "circle-radius": 9, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": cssVar("--highlight"), "circle-stroke-width": 3 },
    });
    map.addLayer({
      id: "sel-line", type: "line", source: "sel", filter: ["==", ["geometry-type"], "LineString"],
      paint: { "line-color": cssVar("--finding"), "line-width": 9, "line-opacity": 0.75 },
    });
    map.addLayer({
      id: "sel-point", type: "circle", source: "sel", filter: ["==", ["geometry-type"], "Point"],
      paint: { "circle-radius": 14, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": cssVar("--finding"), "circle-stroke-width": 4 },
    });
    map.addLayer({
      id: "nodes-hit", type: "circle", source: "nodes",
      filter: ["any", ["!=", ["get", "role"], "junction"], [">=", ["zoom"], 15.5]],
      paint: { "circle-radius": 9, "circle-color": "rgba(0,0,0,0)", "circle-opacity": 0 },
    });
  }

  function tip(title, rows) {
    const d = el("div", { class: "tip" }, el("b", { text: title }));
    for (const [k, v] of rows) {
      d.append(el("span", { class: "k", text: k + ": " }), v, el("br"));
    }
    return d;
  }

  function pipeTip(p) {
    const press = p.pipe_type === "pressurized";
    const slope = !press && Number.isFinite(fnum(p.invert_slope)) ? (-fnum(p.invert_slope) * 1000).toFixed(1) + " \u2030" : "\u2013";
    const depth = `${fmt(p.depth_up, 2)} to ${fmt(p.depth_down, 2)} m`;
    return tip(`Pipe ${p.pipe_id}`, [
      ["Type", (TYPE_LABEL[p.pipe_type] || p.pipe_type) + (p.private ? " (private)" : "")],
      ["DN", Number.isFinite(fnum(p.diameter)) ? Math.round(fnum(p.diameter) * 1000) + " mm" : "\u2013"],
      ["Length", fmt(p.length, 1) + " m"],
      ["Fall (\u2030, positive downstream)", slope],
      ["Depth up/down", depth],
      ["Velocity", fmt(p.velocity, 2) + " m/s"],
      ["Filling d/D", press ? "\u2013" : fmt(p.d_over_D, 2)],
    ]);
  }

  const ROLE_LABEL = {
    building: "Building", junction: "Manhole", pumping_station: "Pumping station",
    lifting_station: "Lifting station", sink: "Sink (outlet)",
  };
  const TYPE_LABEL = { gravity: "Gravity pipe", pressurized: "Pressure main", undesigned: "Not designed" };

  function nodeTip(p) {
    const flags = [];
    if (p.pumping_station) flags.push("Pumping station");
    if (p.lifting_station) flags.push("Lifting station");
    if (p.private_pump) flags.push("Private pump");
    const idx = buildIndex(current);
    const ins = (idx.inByTo.get(p.node_id) || []).map((f) => fnum(f.properties.invert_down)).filter(Number.isFinite);
    const outs = (idx.outByFrom.get(p.node_id) || []).map((f) => fnum(f.properties.invert_up)).filter(Number.isFinite);
    const rows = [
      ["Role", ROLE_LABEL[p.role] || p.role],
      ["Ground", fmt(p.ground, 2) + " m a.s.l."],
    ];
    if (ins.length || outs.length) {
      rows.push(["Invert", `in ${ins.length ? ins.map((v) => v.toFixed(2)).join(", ") : "\u2013"} m \u00b7 out ${outs.length ? outs.map((v) => v.toFixed(2)).join(", ") : "\u2013"} m`]);
    } else {
      rows.push(["Invert", fmt(p.invert, 2) + " m a.s.l."]);
    }
    rows.push(["Depth", fmt(p.depth, 2) + " m"]);
    if (flags.length) rows.push(["Flags", flags.join(", ")]);
    return tip(`Node ${p.node_id}`, rows);
  }

  function hit(point) {
    const nodes = map.getLayer("nodes-hit")
      ? map.queryRenderedFeatures(point, { layers: ["nodes-hit"] })
      : [];
    if (nodes.length) return { kind: "node", props: nodes[0].properties };
    const box = [[point.x - 5, point.y - 5], [point.x + 5, point.y + 5]];
    const layers = PIPE_LAYERS.filter((l) => map.getLayer(l));
    const pipes = layers.length ? map.queryRenderedFeatures(box, { layers }) : [];
    if (pipes.length) return { kind: "pipe", props: pipes[0].properties };
    return null;
  }

  function mapMessage(text) {
    const m = $("map-msg");
    m.textContent = text;
    m.hidden = !text;
  }

  // The OpenFreeMap dark style is near black; lift land and water a little so
  // the network and the roads stay readable. Layer ids differ between styles,
  // so match by type and name and ignore everything else.
  function lightenDarkBasemap() {
    const style = map.getStyle();
    if (!style || !style.layers) return;
    for (const l of style.layers) {
      try {
        if (l.type === "background") {
          map.setPaintProperty(l.id, "background-color", "#26313d");
        } else if (l.type === "fill" && /water/i.test(l.id)) {
          map.setPaintProperty(l.id, "fill-color", "#35556f");
        } else if (l.type === "line" && /road|street|highway|motorway|primary|secondary|tertiary|minor|trunk/i.test(l.id)) {
          map.setPaintProperty(l.id, "line-color", "#5b6b7c");
        }
      } catch (err) { /* layer has no such paint property */ }
    }
  }

  function initMap() {
    if (typeof maplibregl === "undefined") {
      mapMessage("The map library could not be loaded (no internet connection or the CDN is blocked). Metrics, profile and findings still work.");
      $("map-msg").classList.add("fatal");
      $("map").style.display = "none";
      return;
    }
    if (current.metrics.n_pipes === 0 && current.metrics.n_nodes === 0) {
      mapMessage("The network is empty, there is nothing to show on the map.");
    }
    const bounds = allBounds() || areaBounds();
    try {
      map = new maplibregl.Map({
        container: "map",
        style: styleUrl(),
        bounds: bounds || undefined,
        fitBoundsOptions: { padding: 20, maxZoom: 18 },
        cooperativeGestures: true,
        center: bounds ? undefined : [0, 0],
        zoom: bounds ? undefined : 1,
        attributionControl: { compact: true },
      });
    } catch (err) {
      mapMessage("The map could not start: " + err.message);
      return;
    }
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12, maxWidth: "260px" });

    const restore = () => {
      try {
        setupLayers();
        if (effectiveTheme() === "dark" && styleLoaded && !fallbackShown) lightenDarkBasemap();
        mapReady = true;
        updateMapData();
      } catch (err) {
        mapMessage("Map layers failed: " + err.message);
      }
    };
    map.on("style.load", () => { styleLoaded = true; restore(); });
    // A diffed style update does not fire style.load; check again on styledata.
    map.on("styledata", () => {
      if (styleLoaded && !map.getSource("pipes") && map.isStyleLoaded()) restore();
    });
    map.on("error", () => {
      if (!styleLoaded && !fallbackShown) {
        fallbackShown = true;
        mapMessage("The basemap could not be loaded (offline?). The network is shown without a background map.");
        map.setStyle(emptyStyle(), { diff: false });
      } else if (styleLoaded && !tileMsgShown && !navigator.onLine) {
        tileMsgShown = true;
        mapMessage("Some basemap tiles could not be loaded. The network is still correct.");
      }
    });
    let clickTimer = null;
    map.on("dblclick", () => { clearTimeout(clickTimer); });
    map.on("click", (e) => {
      if (!mapReady) return;
      if (e.originalEvent && e.originalEvent.detail > 1) return;
      const h = hit(e.point);
      if (!h) return;
      // wait briefly so that a double click (zoom) does not change the profile
      clearTimeout(clickTimer);
      clickTimer = setTimeout(() => {
        if (h.kind === "node") showProfile(h.props.node_id);
        else showProfile(h.props.from_node);
      }, 260);
    });
    map.on("mousemove", (e) => {
      if (!mapReady) return;
      const h = hit(e.point);
      map.getCanvas().style.cursor = h ? "pointer" : "";
      if (!h) { popup.remove(); return; }
      popup.setLngLat(e.lngLat).setDOMContent(h.kind === "node" ? nodeTip(h.props) : pipeTip(h.props)).addTo(map);
    });
    map.on("mouseout", () => popup.remove());
  }

  function updateMapData() {
    if (!map || !mapReady) return;
    const p = map.getSource("path");
    if (p) p.setData(pathFC);
    const s = map.getSource("sel");
    if (s) s.setData(selFC);
  }

  // ------------------------------------------------------------ legend
  function svg(w, h, children) {
    const ns = "http://www.w3.org/2000/svg";
    const s = document.createElementNS(ns, "svg");
    s.setAttribute("width", w); s.setAttribute("height", h);
    s.setAttribute("viewBox", `0 0 ${w} ${h}`); s.setAttribute("aria-hidden", "true");
    for (const [tag, attrs] of children) {
      const n = document.createElementNS(ns, tag);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      s.append(n);
    }
    return s;
  }

  function renderLegend() {
    const pal = NODE_PALETTE[effectiveTheme()];
    const box = $("legend");
    box.textContent = "";
    const add = (icon, label) => box.append(el("div", { class: "lg" }, icon, el("span", { text: label })));
    const line = (color, w, dash) => svg(30, 12, [["line", { x1: 1, y1: 6, x2: 29, y2: 6, stroke: color, "stroke-width": w, "stroke-dasharray": dash || "", "stroke-linecap": dash ? "butt" : "round" }]]);
    const dot = (fill, stroke, r, sw) => svg(30, 16, [["circle", { cx: 15, cy: 8, r, fill, stroke, "stroke-width": sw }]]);
    DN_LABELS.forEach((l, i) => add(line(pal.dn[i], 3 + i, ""), "Gravity pipe, " + l));
    add(line(cssVar("--pressure"), 4, "6 4"), "Pressure main (dashed)");
    add(line(pal.undesigned, 2.5, "1 5"), "Not designed (dotted)");
    add(svg(30, 16, [["polygon", { points: "6,3 25,8 6,13 10,8", fill: ARROW_COLORS[effectiveTheme()].fill, stroke: ARROW_COLORS[effectiveTheme()].edge, "stroke-width": 2, "stroke-linejoin": "round", "paint-order": "stroke" }]]), "Flow direction (zoom in)");
    add(svg(30, 18, [["polygon", { points: "15,1 18,7 25,7 19.5,11.5 21.5,17 15,13.5 8.5,17 10.5,11.5 5,7 12,7", fill: pal.sink, stroke: pal.stroke, "stroke-width": 1 }]]), "Sink (outlet)");
    add(dot(cssVar("--pressure"), pal.stroke, 7, 2), "Pumping station");
    add(dot(cssVar("--lift"), pal.stroke, 7, 2), "Lifting station");
    add(dot(pal.building, cssVar("--pressure"), 5, 3), "Private pump (building)");
    add(dot(pal.building, pal.stroke, 3.5, 1), "Building");
    add(dot(pal.manhole, pal.manholeStroke, 3.5, 1.2), "Manhole (zoom in)");
    add(line(cssVar("--highlight"), 7, ""), "Path of the shown profile");
    add(line(cssVar("--finding"), 7, ""), "Selected finding");
  }

  // ------------------------------------------------------------ profile
  let currentTrace = null;

  function pipeSlopePermille(seg) {
    const inv = seg.invert.filter((p) => p[1] !== null);
    if (inv.length < 2) return null;
    const a = inv[0]; const b = inv[inv.length - 1];
    if (b[0] === a[0]) return null;
    return (-(b[1] - a[1]) / (b[0] - a[0])) * 1000;
  }

  const KIND_LABEL = {
    start: "Start", manhole: "Manhole", drop: "Drop manhole", lifting_station: "Lifting station",
    pumping_station: "Pumping station", pump_relay: "Pump relay (no lift on this path)", sink: "Sink",
  };

  function profileMessage(text) {
    const m = $("profile-msg");
    m.textContent = text || "";
    m.hidden = !text;
  }

  function renderProfile(trace) {
    currentTrace = trace;
    const segs = trace.segments;
    const sub = $("profile-sub");
    renderProfileTable(trace);
    if (typeof Plotly === "undefined") {
      profileMessage("The plot library could not be loaded (no internet connection or the CDN is blocked). The table below holds the same numbers.");
      $("profile").style.display = "none";
      $("profile-msg").classList.add("fatal");
      sub.textContent = "";
      return;
    }
    if (segs.length === 0) {
      Plotly.purge("profile");
      sub.textContent = `Node ${trace.start}`;
      profileMessage(`Node ${trace.start} has no outgoing pipe. It is a sink or an unconnected node, so there is no profile.`);
      return;
    }
    profileMessage("");
    const count = (k) => trace.markers.filter((m) => m.kind === k).length;
    const relays = count("pump_relay");
    const pumping = count("pumping_station") + relays;
    const lifting = count("lifting_station");
    const drops = count("drop");
    const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
    const parts = [plural(segs.length, "pipe")];
    if (pumping) parts.push(plural(pumping, "pumping station") + (relays ? ` (${relays} relay${relays === 1 ? "" : "s"})` : ""));
    if (lifting) parts.push(plural(lifting, "lifting station"));
    parts.push(plural(drops, "drop"));
    sub.textContent = `From ${trace.start} to sink ${trace.sink}: ${fmt(trace.total_length, 0)} m, ${parts.join(", ")}.`;
    $("profile-note").hidden = !segs.some((x) => x.pipe_type === "pressurized");

    const gaps = (pick, filter) => {
      const x = []; const y = []; const cd = [];
      for (const s of segs) {
        if (!filter(s)) continue;
        const pts = pick(s);
        if (!pts.length) continue;
        if (x.length) { x.push(null); y.push(null); cd.push(null); }
        const slope = s.pipe_type === "pressurized" ? null : pipeSlopePermille(s);
        for (const p of pts) {
          x.push(p[0]); y.push(p[1]);
          cd.push([s.pipe_id, s.diameter === null ? "\u2013" : Math.round(s.diameter * 1000), slope === null ? "\u2013" : slope.toFixed(1)]);
        }
      }
      return { x, y, cd };
    };
    const isGrav = (s) => s.pipe_type !== "pressurized";
    const isPress = (s) => s.pipe_type === "pressurized";
    const ground = gaps((s) => s.ground, () => true);
    const crownG = gaps((s) => s.crown, isGrav);
    const invG = gaps((s) => s.invert, isGrav);
    const crownP = gaps((s) => s.crown, isPress);
    const invP = gaps((s) => s.invert, isPress);
    const water = gaps((s) => s.water, isGrav);
    // One closed polygon per pipe (crown forward, invert back), null-separated.
    const band = (filter) => {
      const x = []; const y = [];
      for (const s of segs) {
        if (!filter(s) || s.invert.length < 2) continue;
        const cr = s.crown.filter((p) => p[1] !== null);
        const iv = s.invert.filter((p) => p[1] !== null).reverse();
        if (cr.length < 2 || iv.length < 2) continue;
        if (x.length) { x.push(null); y.push(null); }
        for (const p of cr.concat(iv, [cr[0]])) { x.push(p[0]); y.push(p[1]); }
      }
      return { x, y };
    };
    const bandG = band(isGrav);
    const bandP = band(isPress);

    const zs = [];
    for (const arr of [ground.y, invG.y, invP.y, crownG.y, crownP.y]) {
      for (const v of arr) if (v !== null && Number.isFinite(v)) zs.push(v);
    }
    const zmin = zs.length ? Math.min(...zs) : 0;
    const zmax = zs.length ? Math.max(...zs) : 1;
    const pad = Math.max(1, (zmax - zmin) * 0.08);
    const ymin = zmin - pad;
    const ymax = zmax + pad;

    const fg = cssVar("--fg");
    const earth = cssVar("--earth");
    const pipeFill = cssVar("--pipe-fill");
    const pressure = cssVar("--pressure");
    const waterC = cssVar("--water");
    const lift = cssVar("--lift");
    const dropC = cssVar("--drop");
    const palSink = NODE_PALETTE[effectiveTheme()].sink;
    const xEnd = trace.total_length;

    const hoverPipe = "<b>%{customdata[0]}</b> DN %{customdata[1]} mm<br>Chainage %{x:.1f} m<br>Invert %{y:.2f} m a.s.l.<br>Fall %{customdata[2]} \u2030<extra></extra>";
    const traces = [
      { x: [0, xEnd], y: [ymin, ymin], mode: "lines", line: { width: 0 }, hoverinfo: "skip", showlegend: false },
      { x: ground.x, y: ground.y, mode: "lines", name: "Ground", line: { color: cssVar("--muted"), width: 1.5 },
        fill: "tonexty", fillcolor: toRgba(earth, 0.55), hovertemplate: "Ground %{y:.2f} m a.s.l.<br>Chainage %{x:.1f} m<extra></extra>" },
      { x: bandG.x, y: bandG.y, mode: "lines", fill: "toself", name: "Gravity pipe (invert to crown)",
        line: { width: 0 }, fillcolor: toRgba(pipeFill, 0.5), hoverinfo: "skip", showlegend: bandG.x.length > 0 },
      { x: crownG.x, y: crownG.y, mode: "lines", line: { color: pipeFill, width: 1 }, hoverinfo: "skip", showlegend: false },
      { x: invG.x, y: invG.y, customdata: invG.cd, mode: "lines", showlegend: false,
        line: { color: pipeFill, width: 2 }, hovertemplate: hoverPipe },
      { x: bandP.x, y: bandP.y, mode: "lines", fill: "toself", name: "Pressure main",
        line: { width: 0 }, fillcolor: toRgba(pressure, 0.3), hoverinfo: "skip", showlegend: bandP.x.length > 0 },
      { x: crownP.x, y: crownP.y, mode: "lines", line: { color: pressure, width: 1.5, dash: "dash" }, hoverinfo: "skip", showlegend: false },
      { x: invP.x, y: invP.y, customdata: invP.cd, mode: "lines", showlegend: false,
        line: { color: pressure, width: 1.5, dash: "dash" }, hovertemplate: hoverPipe },
      { x: water.x, y: water.y, mode: "lines", name: "Water level (peak flow)", line: { color: waterC, width: 2 }, hoverinfo: "skip" },
    ];

    const markerText = (m) =>
      `<b>${KIND_LABEL[m.kind] || m.kind}: ${m.node_id}</b><br>Chainage ${fmt(m.chainage, 1)} m<br>` +
      `Ground ${fmt(m.ground, 2)} m a.s.l.<br>Invert in ${fmt(m.invert_in, 2)} m, out ${fmt(m.invert_out, 2)} m`;
    const kindTrace = (kinds, name, symbol, color, size, open) => {
      const ms = trace.markers.filter((m) => kinds.includes(m.kind) && m.ground !== null);
      if (!ms.length) return null;
      return {
        x: ms.map((m) => m.chainage), y: ms.map((m) => m.ground), mode: "markers", name,
        marker: { symbol: open ? symbol + "-open" : symbol, size, color, line: { color: fg, width: open ? 2 : 1 } },
        text: ms.map(markerText), hovertemplate: "%{text}<extra></extra>",
      };
    };
    for (const t of [
      kindTrace(["start"], "Start", "circle", fg, 10),
      kindTrace(["sink"], "Sink", "star", palSink, 15),
      kindTrace(["lifting_station", "pumping_station"], "Station", "diamond", lift, 11),
      kindTrace(["pump_relay"], "Pump relay", "diamond", pressure, 10, true),
      kindTrace(["drop"], "Drop manhole", "triangle-down", dropC, 9),
    ]) {
      if (t) traces.push(t);
    }

    const shapes = [];
    for (const m of trace.markers) {
      const vals = [m.invert_in, m.invert_out].filter((v) => v !== null);
      if (m.ground === null || !vals.length) continue;
      shapes.push({ type: "line", x0: m.chainage, x1: m.chainage, y0: Math.min(...vals), y1: m.ground,
        line: { color: cssVar("--muted"), width: 1 }, layer: "below" });
      if (m.invert_in !== null && m.invert_out !== null && (m.kind === "lifting_station" || m.kind === "pumping_station" || m.kind === "drop")) {
        shapes.push({ type: "line", x0: m.chainage, x1: m.chainage, y0: m.invert_in, y1: m.invert_out,
          line: { color: m.kind === "drop" ? dropC : lift, width: 5 } });
      }
    }

    const narrow = $("profile").clientWidth < 520;
    const layout = {
      margin: { l: narrow ? 48 : 64, r: 12, t: 8, b: narrow ? 250 : 90 },
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: fg, size: 12 },
      xaxis: { title: { text: "Chainage from start (m)" }, gridcolor: cssVar("--plot-grid"), zeroline: false, range: [0, xEnd], automargin: true },
      yaxis: { title: { text: "Elevation (m a.s.l.)" }, gridcolor: cssVar("--plot-grid"), zeroline: false, range: [ymin, ymax], automargin: true },
      legend: { orientation: "h", y: narrow ? -0.5 : -0.2, yanchor: "top", font: { size: 11 } },
      hovermode: "closest",
      hoverlabel: { font: { size: 12 } },
      shapes: shapes,
    };
    $("profile").style.display = "";
    const gd = $("profile");
    Plotly.react(gd, traces, layout, {
      responsive: true, displaylogo: false,
      modeBarButtonsToRemove: ["lasso2d", "select2d"],
    }).then(() => {
      updateExaggeration();
      if (!gd._pysewerBound) {
        gd._pysewerBound = true;
        gd.on("plotly_afterplot", updateExaggeration);
      }
    });
    $("profile").setAttribute("aria-label",
      `Longitudinal profile from ${trace.start} to sink ${trace.sink}, ${fmt(trace.total_length, 0)} metres long.`);
  }

  // Vertical exaggeration from the axis ranges and the plot area in pixels.
  function updateExaggeration() {
    const gd = $("profile");
    const fl = gd && gd._fullLayout;
    const box = $("vexag");
    if (!fl || !fl._size || !fl.xaxis || !fl.yaxis) { box.textContent = ""; return; }
    const xr = Math.abs(fl.xaxis.range[1] - fl.xaxis.range[0]);
    const yr = Math.abs(fl.yaxis.range[1] - fl.yaxis.range[0]);
    if (!(xr > 0 && yr > 0 && fl._size.w > 0 && fl._size.h > 0)) { box.textContent = ""; return; }
    const factor = (fl._size.h * xr) / (fl._size.w * yr);
    box.textContent = `Vertical scale \u2248 ${factor >= 10 ? factor.toFixed(0) : factor.toFixed(1)}\u00d7 horizontal.`;
  }

  function renderProfileTable(trace) {
    const t = $("profile-table");
    t.textContent = "";
    const head = el("tr");
    for (const h of ["Pipe", "Type", "DN (mm)", "Length (m)", "From chainage (m)", "To chainage (m)", "Fall (\u2030, positive downstream)", "Velocity (m/s)", "d/D (-)"]) {
      head.append(el("th", { scope: "col", text: h }));
    }
    t.append(el("thead", null, head));
    const body = el("tbody");
    for (const s of trace.segments) {
      const slope = s.pipe_type === "pressurized" ? null : pipeSlopePermille(s);
      const cells = [
        s.pipe_id, TYPE_LABEL[s.pipe_type] || s.pipe_type,
        s.diameter === null ? "\u2013" : String(Math.round(s.diameter * 1000)),
        fmt(s.chainage_end - s.chainage_start, 1), fmt(s.chainage_start, 1), fmt(s.chainage_end, 1),
        slope === null ? "\u2013" : slope.toFixed(1), fmt(s.velocity, 2), s.pipe_type === "pressurized" ? "\u2013" : fmt(s.d_over_D, 2),
      ];
      const tr = el("tr");
      cells.forEach((c, i) => tr.append(el("td", { class: i >= 2 ? "num" : "", text: c })));
      body.append(tr);
    }
    t.append(body);
  }

  function showProfile(nodeId, opts) {
    const msg = $("node-input");
    let trace;
    try {
      trace = traceProfile(DATA, current.name, nodeId);
    } catch (err) {
      profileMessage("No profile: " + err.message);
      return null;
    }
    selectedStart = trace.start;
    msg.value = trace.start;
    renderProfile(trace);
    // highlight the path on the map
    const idx = buildIndex(current);
    const feats = trace.pipe_ids.map((id) => {
      const f = idx.pipeById.get(id);
      return { type: "Feature", properties: { pipe_id: id }, geometry: f.geometry };
    });
    const startNode = idx.nodeById.get(trace.start);
    if (startNode) feats.push({ type: "Feature", properties: {}, geometry: startNode.geometry });
    pathFC = { type: "FeatureCollection", features: feats };
    updateMapData();
    return trace;
  }

  // ------------------------------------------------------------ findings table
  const CHECK_LABEL = {
    depth_exceeds_tmax: "Trench deeper than tmax",
    cover_below_min: "Cover over crown too low",
    velocity_low: "Velocity too low",
    velocity_high: "Velocity too high",
    fill_above_target: "Filling d/D above target",
    invert_rises: "Invert rises downstream",
    short_pipe: "Pipe too short",
    undesigned_pipe: "Pipe not designed",
    unrouted_building: "Building not connected",
    "engine:max_slope": "Slope too steep (engine)",
    "engine:min_slope": "Slope too flat (engine)",
    "engine:min_cover": "Cover too low (engine)",
    "engine:min_pipe_length": "Pipe too short (engine)",
    "engine:velocity_min": "Velocity too low (engine)",
    "engine:velocity_max": "Velocity too high (engine)",
    "engine:depth_ratio": "Filling d/D too high (engine)",
    "engine:no_diameter_meets_depth_ratio": "No diameter fits d/D (engine)",
    "engine:gradient_below_dts": "Gradient too low (engine)",
  };
  function checkLabel(c) {
    return CHECK_LABEL[c] || c;
  }

  const sortState = { key: "severity", dir: 1 };

  function findingCompare(a, b) {
    const k = sortState.key;
    let r;
    if (k === "severity") r = SEV_ORDER[a.severity] - SEV_ORDER[b.severity];
    else if (k === "value") r = (fnum(a.value) || 0) - (fnum(b.value) || 0);
    else r = String(a[k]).localeCompare(String(b[k]), undefined, { numeric: true });
    if (r === 0) r = String(a.element_id).localeCompare(String(b.element_id), undefined, { numeric: true });
    return r * sortState.dir;
  }

  function selectFinding(f, row) {
    const idx = buildIndex(current);
    let feature = null;
    let bounds = null;
    if (f.element_type === "pipe") {
      feature = idx.pipeById.get(f.element_id);
      if (feature) bounds = coordsBounds(feature.geometry.coordinates);
    } else {
      feature = idx.nodeById.get(f.element_id);
    }
    document.querySelectorAll("#findings tbody tr.sel").forEach((r) => r.classList.remove("sel"));
    if (row) row.classList.add("sel");
    if (!feature) return;
    selFC = { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: feature.geometry }] };
    updateMapData();
    if (map) {
      if (bounds) map.fitBounds(bounds, { padding: 40, maxZoom: 19, duration: 600 });
      else map.flyTo({ center: feature.geometry.coordinates, zoom: Math.max(map.getZoom(), 18), duration: 600 });
      $("map").scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  function renderFindings() {
    const sev = $("sev-filter").value;
    const chk = $("check-filter").value;
    let rows = current.findings.filter((f) => (sev === "all" || f.severity === sev) && (chk === "all" || f.check === chk));
    rows = rows.slice().sort(findingCompare);
    const tbody = document.querySelector("#findings tbody");
    tbody.textContent = "";
    $("findings-count").textContent = current.findings.length === 0
      ? ""
      : `${rows.length} of ${current.findings.length} findings shown.`;
    if (current.findings.length === 0) {
      const empty = current.metrics.n_pipes === 0
        ? "Nothing to check: the network has no pipes."
        : "No problems found.";
      const td = el("td", { colspan: "6", text: empty });
      tbody.append(el("tr", null, td));
      return;
    }
    if (rows.length === 0) {
      tbody.append(el("tr", null, el("td", { colspan: "6", text: "No findings match the filter." })));
      return;
    }
    for (const f of rows) {
      const tr = el("tr");
      const btn = el("button", { type: "button", class: "btn link", "aria-label": `Show ${f.element_type} ${f.element_id} on the map`, text: `${f.element_id} \u00b7 ${f.element_type}` });
      const unit = f.unit ? " " + f.unit : "";
      tr.append(
        el("td", { "data-label": "Severity" }, el("span", { class: `sev ${f.severity}`, text: f.severity })),
        el("td", { "data-label": "Check", title: f.check, text: checkLabel(f.check) }),
        el("td", { "data-label": "Element" }, btn),
        el("td", { class: "num nowrap", "data-label": "Value", text: f.value === null ? "\u2013" : fmt(f.value, 3) + unit }),
        el("td", { class: "num nowrap", "data-label": "Limit", text: f.limit === null ? "\u2013" : fmt(f.limit, 3) + unit }),
        el("td", { "data-label": "Message", text: f.message })
      );
      btn.addEventListener("click", (e) => { e.stopPropagation(); selectFinding(f, tr); });
      tr.addEventListener("click", () => selectFinding(f, tr));
      tbody.append(tr);
    }
  }

  function initFindings() {
    const checks = Array.from(new Set(current.findings.map((f) => f.check))).sort();
    const sel = $("check-filter");
    for (const c of checks) sel.append(el("option", { value: c, text: checkLabel(c) }));
    $("sev-filter").addEventListener("change", renderFindings);
    sel.addEventListener("change", renderFindings);
    document.querySelectorAll("#findings .sort").forEach((b) => {
      b.addEventListener("click", () => {
        const key = b.getAttribute("data-key");
        if (sortState.key === key) sortState.dir = -sortState.dir;
        else { sortState.key = key; sortState.dir = 1; }
        document.querySelectorAll("#findings th").forEach((th) => th.removeAttribute("aria-sort"));
        b.parentElement.setAttribute("aria-sort", sortState.dir === 1 ? "ascending" : "descending");
        document.querySelectorAll("#findings .arrow").forEach((a) => (a.textContent = ""));
        b.querySelector(".arrow").textContent = sortState.dir === 1 ? "▲" : "▼";
        renderFindings();
      });
    });
    const ss = $("sort-select");
    ss.addEventListener("change", () => {
      sortState.key = ss.value;
      sortState.dir = 1;
      document.querySelectorAll("#findings th").forEach((th) => th.removeAttribute("aria-sort"));
      document.querySelectorAll("#findings .arrow").forEach((a) => (a.textContent = ""));
      const b = document.querySelector(`#findings .sort[data-key="${ss.value}"]`);
      if (b) {
        b.parentElement.setAttribute("aria-sort", "ascending");
        b.querySelector(".arrow").textContent = "\u25B2";
      }
      renderFindings();
    });
    const first = document.querySelector('#findings .sort[data-key="severity"]');
    first.parentElement.setAttribute("aria-sort", "ascending");
    first.querySelector(".arrow").textContent = "▲";
    renderFindings();
  }

  // ------------------------------------------------------------ node picker, theme
  function initPicker() {
    const idx = buildIndex(current);
    if (current.metrics.n_pipes === 0) {
      for (const id of ["node-input", "node-go", "node-longest"]) $(id).disabled = true;
      $("node-input").placeholder = "no pipes";
    }
    const list = $("node-list");
    if (idx.nodeById.size <= 600) {
      for (const id of idx.nodeById.keys()) list.append(el("option", { value: id }));
    }
    const go = () => {
      const id = $("node-input").value.trim();
      if (!id) return;
      showProfile(id);
    };
    $("node-go").addEventListener("click", go);
    $("node-input").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    $("node-longest").addEventListener("click", () => {
      if (DEFAULT_PROFILE) showProfile(DEFAULT_PROFILE.start);
      else profileMessage("No profile: the network has no pipes.");
    });
  }

  function initTheme() {
    const btn = $("theme-toggle");
    const label = () => {
      const t = document.documentElement.getAttribute("data-theme");
      btn.textContent = "Theme: " + (t || "auto");
    };
    label();
    btn.addEventListener("click", () => {
      const t = document.documentElement.getAttribute("data-theme");
      const next = t === null ? "light" : t === "light" ? "dark" : null;
      if (next) document.documentElement.setAttribute("data-theme", next);
      else document.documentElement.removeAttribute("data-theme");
      label();
      onThemeChange();
    });
    if (window.matchMedia) {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const h = () => { if (!document.documentElement.getAttribute("data-theme")) onThemeChange(); };
      if (mq.addEventListener) mq.addEventListener("change", h);
    }
  }

  function onThemeChange() {
    renderLegend();
    if (map && !fallbackShown) {
      mapReady = false;
      map.setStyle(styleUrl(), { diff: false });
    } else if (map) {
      mapReady = false;
      map.setStyle(emptyStyle(), { diff: false });
    }
    if (currentTrace && currentTrace.segments.length) renderProfile(currentTrace);
  }


  // ------------------------------------------------------------ start
  window.pysewerReport = {
    traceProfile: (variant, nodeId) => traceProfile(DATA, variant, nodeId),
    showProfile: showProfile,
    data: DATA,
    params: PARAMS,
    get currentTrace() { return currentTrace; },
  };

  function boot() {
    if (window.matchMedia && window.matchMedia("(max-width: 600px)").matches) {
      const lg = document.querySelector("details.legend");
      if (lg) lg.removeAttribute("open");
    }
    initTheme();
    renderHeader();
    renderBanner();
    renderMetrics();
    renderLegend();
    initFindings();
    initPicker();
    initMap();
    if (DEFAULT_PROFILE) {
      showProfile(DEFAULT_PROFILE.start);
    } else {
      $("profile").style.display = "none";
      profileMessage("No profile: the network has no pipes.");
    }
  }

  try {
    boot();
  } catch (err) {
    const box = document.createElement("div");
    box.className = "fatal";
    box.setAttribute("role", "alert");
    box.textContent = "The report could not be displayed: " + err.message;
    document.body.prepend(box);
    throw err;
  }
})();
