# Changelog

All notable changes to pysewer are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- `clustering: "centers"` no longer strands buildings. A cluster center
  whose pre-computed closest road edge had already been split by an
  earlier center failed to connect, linked to itself (self-loop) and,
  with the buildings attached to it, formed an island without a road
  link. `rsph_tree` then kept only the largest component and dropped
  those buildings without a message (13 of 800 on the example data).
  `add_node` now looks up a stale closest edge again and never treats a
  node as its own nearest cluster center.
- `rsph_tree` never drops terminals silently: buildings without a path
  to a sink are listed in `graph["unrouted_terminals"]` with a warning,
  and `strict=True` raises `UnroutedTerminalsError` (a `ValueError`).
  It keeps every component that contains a sink instead of the largest
  component, and `find_rsph_path` keeps terminals and distances aligned
  when a terminal is skipped (previously the wrong terminal was chosen,
  and with sinks in separate components nothing was routed).
- `rsph_tree_fast` accepts a list of sinks as documented (it routed no
  building at all for a list) and only treats a missing path as
  unrouted; unrouted terminals are reported as in `rsph_tree`.

### Added

- `get_sewer_info` reports `Connected Buildings` and
  `Unconnected Buildings` next to `Total Buildings`.

### Changed

- Release workflow: a version tag now also creates the GitHub release
  (notes from this CHANGELOG, wheel and sdist attached) after the PyPI
  upload succeeds.

## [0.3.0] - 2026-10-21

Follow-ups from the Elan integration meeting (2026-06-08) and the Elan
bug report (2026-08-06).

### Added

- Benchmark suite (`benchmarks/`): declarative scenario YAMLs, a CLI
  runner writing JSON + markdown summaries to `work/benchmarks/results`,
  and a beginner walkthrough notebook. `small_*` scenarios run on the
  repository test data; `elan_*` scenarios use the (uncommitted) French
  dataset.
- Config value validation at load time: negative
  `tmin`/`tmax`/`inflow_trench_depth`/`min_trench_depth`/`min_cover`/
  `min_pipe_length` are rejected, and `tmax > tmin`,
  `velocity_max > velocity_min` are enforced.

- Configurable rounding of exported float attributes
  (`export.round_decimals`, default 3 decimals, `peak_flow` 6, `slope` 5);
  also applies to serialized profile tuples. Requested by the Elan team to
  avoid 7+ decimal places in GPKG outputs.
- `plot` optional dependency extra: matplotlib is no longer a required
  dependency; `pysewer.plotting` is imported lazily so `import pysewer`
  works without matplotlib installed (a "light" install for embedding,
  e.g. the Elan QGIS plugin).

### Changed

- `write_gdf_to_gpkg` / `write_gdf_to_shp` no longer mutate the caller's
  GeoDataFrame (JSON conversion and rounding happen on a copy).
- `needs_pump` is no longer present on the designed network's edges (and
  therefore no longer exported): it is a routing-internal terrain flag
  (pump-penalty input) that was repeatedly misread as the pump indicator.
  On the design, `pressurized` plus the `pumping_station`/
  `lifting_station` node flags are authoritative. The flag remains on the
  connection graph.

### Added (Butler conformity, P1)

- Variable peak factor: `optimization.peak_factor_method` supports
  `babbitt` (5/P^0.2), `harman` (1+14/(4+√P)) and `gifft` (5/P^(1/6)) in
  addition to the legacy `constant`; the factor is evaluated per node from
  the cumulative upstream population, capped at `peak_factor_max`
  (BS EN 752 multiple: 6) and stored as a `peak_factor` node attribute.
- Infiltration allowance: `optimization.infiltration_fraction` (Butler
  Eq. 9.1; conventional 0.1) — added to the peak unfactored:
  `Q_peak = PF·PG + I`. Default 0 keeps previous behavior.
- Self-cleansing at intermittent heads: below
  `small_sewer_flow_threshold` (default 1 L/s — Butler's small-sewer
  regime) the physically meaningless steady `velocity_min` flag is no
  longer recorded; the BS EN 752 deemed-to-satisfy minimum gradient is
  checked instead (`dts_min_gradient`, default 1:150; violation
  `gradient_below_dts`).

### Changed (design realism — self-cleansing minimum slope)

- Minimum laying gradient is now **diameter-dependent** by default
  (`optimization.min_slope_model: "self_cleansing"`). The uniform
  `min_slope` (1%) was applied to every diameter, which over-buries trunks
  on flat terrain until burial reaches `tmax` and forces a lifting station
  roughly every `tmax/min_slope` of flow path — the source of implausibly
  high lift counts on flat networks. Larger pipes self-cleanse at much
  flatter grades, so `min_self_cleansing_slope(D)` inverts Manning at full
  bore (`S = (velocity_min·n / (D/4)**(2/3))**2`, floored at
  `min_slope_abs`, default 1:2000). Values match the Butler & Davies /
  BS EN 752 deemed-to-satisfy gradients within ~10% (DN200 1:222, DN300
  1:382, DN500 1:755). On the Wasweta II benchmark this cuts lifting
  stations 124 → 96 and pumping stations 12 → 9 with routing/topology
  unchanged. Set `min_slope_model: "uniform"` to restore the legacy
  single-gradient behaviour.
- Under the self-cleansing model the partial-flow `velocity_min` acceptance
  flag is not recorded: every pipe is laid at its diameter's self-cleansing
  gradient where terrain permits, so the gradient itself is the
  deemed-to-satisfy criterion (Butler / BS EN 752). The steady velocity at
  foul design flow is always below 0.7 m/s in a minimally graded sewer, so it
  is not a meaningful additional acceptance test (this is exactly why the
  standard specifies minimum *gradients*, not a design-flow velocity).
  `velocity_max` still applies. The legacy `uniform` model keeps its
  `velocity_min` / small-sewer `gradient_below_dts` reporting.
- Default `tmax` raised 6 m → 8 m (the historical Pysewer-1 value). A gravity
  pipe is buried down to `tmax` before a lifting station is forced, so a
  deeper limit converts many shallow lifts into a modest deep-trench tail —
  the median trench stays ~0.4 m; only the deep p95 grows (~4.5 m → ~5.8 m).
  On Wasweta II (self-cleansing model, routing rebuilt at each `tmax`) this
  takes lifting stations 96 → 70 and pumping stations 9 → 5. Combined with the
  self-cleansing slope, total stations fall from 136 (uniform, `tmax` 6:
  12 pumps / 124 lifts) to 75 (5 / 70), a 45% reduction. `tmax` remains a
  per-project knob — 10 m cuts stations further where deeper excavation
  (shoring / groundwater) is acceptable.

### Fixed

- Pipe geometries no longer contain float-noise duplicate vertices or
  near-zero-length segments when a connection point lands on an existing road
  vertex (#4). New helpers `dedupe_coords` / `points_close` (tolerance
  `COORD_TOL` = 1e-6 CRS units) are applied where pipe LineStrings are built.

- Buildings and cluster centers now connect to the road edge that is truly
  nearest (point-to-segment distance), with ties broken by edge endpoint
  coordinates. The previous nearest-*vertex* search (`ckdnearest`, a KD-tree
  over edge vertices) stored every shared junction once per edge, and which
  copy it returned on an exact tie depended on the C++ standard library: the
  same inputs and library versions gave different networks on macOS-arm64
  (95 edges) and Linux-x86_64 (101 edges) for the example dataset. Both now
  give the same network (106 edges, 3 pumping + 1 lifting station, 2017 m;
  previously 2060 m / 2066 m). It also no longer attaches a point to a short
  edge whose vertex happens to be near while a long edge passes closer.
  New helper `closest_edges`; `ckdnearest` is kept but no longer used for
  edge selection. Networks generated by earlier versions will differ.

- Capacity criterion and `d_over_D` now use the true proportional flow
  depth (Butler / BS EN 752). Previously the exported `d_over_D` was the
  flow ratio `Q/Q_full` and the sizing check `Q/Q_full <= max_depth_ratio`
  effectively enforced d/D ≈ 0.65 instead of the intended 0.75 (for
  circular pipes, d/D = 0.75 corresponds to Q/Q_full ≈ 0.912). Sizing now
  selects the smallest diameter conveying the design flow at
  d/D ≤ `max_depth_ratio` (exact wetted-angle capacity), and `d_over_D`
  reports the normal-flow proportional depth. Realistic designs are
  unchanged (capacity rarely binds); near-capacity pipes may now
  legitimately stay one size smaller.
- Default `roughness` corrected from `0.0015` to `0.013` in
  `settings.yaml`. `0.0015` is a Colebrook-White absolute roughness height
  (1.5 mm) that was being used as Manning's *n* (which should be ~0.013),
  understating friction ~8× — this inflated pipe capacity and produced
  unphysical design velocities (up to ~9 m/s). It did **not** affect chosen
  diameters (flows on typical small catchments run the pipes far below
  capacity regardless), but it made the reported `velocity` and velocity
  `hydraulic_violations` unreliable. Config validation now rejects a
  `roughness` outside the plausible Manning's-n range [0.008, 0.1] to catch
  the k_s/n confusion; the Manning docstrings clarify the expected quantity.
- User-defined config values were partially ignored: `needs_pump()` used
  falsy `or` fallbacks, so a custom `tmin` never applied to head edges
  (`inflow_trench_depth=0` was swallowed before the documented "0 → tmin"
  branch) and explicit zero `min_slope`/`tmax`/`tmin` arguments were
  replaced by defaults.
- `ModelDomain` froze `pump_penalty` at construction; config overrides set
  afterwards (e.g. `set_custom_config` after creating the model domain)
  had no effect on routing weights. `pump_penalty` is now resolved lazily
  from the current config unless explicitly set.

## [0.2.0] - 2026-07-24

Repository migrated from `git.ufz.de/despot/pysewer` to
[codebase.helmholtz.cloud/wasp/pysewer](https://codebase.helmholtz.cloud/wasp/pysewer)
with full history.

### Added

- External contributions from the ELAN project
  (Jacky Volpes, [Djedouas/pysewer](https://github.com/Djedouas/pysewer),
  branch `elan-sprint-18`):
  - Support for an inhabitants number given as a buildings attribute
    (`optimization.inhabitants_dwelling_attribute_name`) in peak flow
    estimation.
  - Multi-layer GeoPackage export: `write_gdf_to_gpkg` accepts a layer name
    and an explicit CRS.
  - Fiona schema mapping for list and bool column dtypes.
  - `total_static_head` attribute on pumping and lifting stations.
  - `sink_coords` attribute on all routed nodes and edges, identifying the
    sink each element drains to.
- Hydraulic constraint checking during network generation and diameter
  selection: minimum cover, minimum pipe length, maximum slope, velocity
  bounds (`velocity_min`/`velocity_max`) and maximum depth ratio, recorded
  per edge as `constraint_violations` / `hydraulic_violations`.
- Centralized runtime configuration (`pysewer.config`) with
  `get_config`/`set_config` and YAML-based settings, replacing scattered
  defaults.
- Tests for the ELAN-contributed features, hydraulic constraints, pump
  penalty routing, and configuration management (41 tests).
- `CITATION.cff` referencing the JOSS article
  ([10.21105/joss.06430](https://doi.org/10.21105/joss.06430)) and this
  changelog.

### Changed

- Packaging modernized: `setup.py` and the conda recipe (`bld.bat`,
  `build.sh`) replaced by `pyproject.toml`; environment management moved to
  the two-layer mamba (`environment.yml`, native geospatial stack) +
  uv (`uv pip install -e '.[dev]'`, pure-Python layer) workflow with
  `conda-lock` for reproducibility (managed via `hpc-env`).
- Python baseline raised to 3.11 in the conda layer (package still declares
  `requires-python >= 3.10`); geospatial stack unpinned so conda-forge
  co-resolves GDAL/PROJ/GEOS (previously geopandas 0.9 / shapely 1.x era).
- Pump requirement logic reworked: slope calculated correctly along the
  DEM profile and `needs_pump` assigned consistently; pump penalty
  demonstrably influences shortest-path routing.

### Fixed

- Trench depth profile and mean trench depth for pressurized edges
  (ELAN contribution).
- `Roads` and `Buildings` accept `pathlib.Path` inputs, not only `str`.
- `min_cover` default (1.5 m) contradicted `tmin`/`inflow_trench_depth`
  (0.25 m), flagging every gravity edge with a cover violation and forcing
  pumps network-wide. Cover violations are still recorded but no longer
  force `needs_pump`; the default is aligned with `tmin`.
- Built Sphinx HTML removed from version control (`docs/build/`).

## [0.1.20] - 2024-12

Last release of the JOSS-reviewed line
([Zenodo 10.5281/zenodo.14355668](https://doi.org/10.5281/zenodo.14355668)):
automated routing and optimization of sewer networks with RSPH and
RSPH-fast solvers, DEM-based trench and pump placement, dry-weather-flow
based dimensioning, plotting and GPKG/SHP/Parquet export.
