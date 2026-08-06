# Changelog

All notable changes to pysewer are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

### Fixed

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
