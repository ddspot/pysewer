<!-- SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
SPDX-License-Identifier: GPL-3.0-only -->

# pysewer benchmarks

Reproducible network-generation runs used to (a) catch behavioral
regressions between pysewer versions and (b) compare parameter settings
(e.g. `tmax`, diameter lists, pump penalty) on real datasets. Started in
response to the Elan project's Pysewer-1 vs Pysewer-2 comparisons
(bug report 2026-08-06).

## Layout

```
benchmarks/
├── scenarios/        # declarative YAML: dataset + config overrides per run
├── scripts/          # CLI runners (for developers, CI and coding agents)
│   └── run_scenarios.py
└── notebooks/        # guided walkthroughs (for Python/pysewer beginners)
```

- **Scripts** are the canonical way to produce numbers: deterministic,
  parameterized, machine-readable output (JSON + a markdown summary table).
- **Notebooks** teach the same workflow step by step with plots and
  explanations; they are *not* the source of record for benchmark numbers.

## Running

```shell
# all scenarios whose input data is available:
python benchmarks/scripts/run_scenarios.py

# specific scenarios:
python benchmarks/scripts/run_scenarios.py small_tmax8 small_tmax3
```

Results land in `work/benchmarks/results/` (git-ignored scratch, see the
`work/` convention) — one JSON per scenario plus `summary.md` comparing all
scenarios of the invocation.

## Data

- `small_*` scenarios use the repository's own `tests/test_data` — they run
  on any clone with no extra data and are the ones suitable for CI.
- `elan_*` scenarios use the French dataset provided by the Elan project
  (REVERSAAL/INRAE + Oslandia). This data is **not ours to publish** and is
  therefore never committed; stage it locally under
  `work/benchmarks/elan/` (`DEM_5m.tif`, `sewer_inputs.gpkg`,
  `sewer-outputs.gpkg`). Scenarios whose data is missing are skipped with a
  note. Contact the Elan team (or D. Despot) for access.

## Adding a scenario

Drop a YAML file into `scenarios/`:

```yaml
name: small_tmax3
description: repo test data, shallow trenching limit
data:
  dem: tests/test_data/dem.tif
  roads: tests/test_data/roads_clipped.shp
  buildings: tests/test_data/buildings_clipped.shp
  sink: [691350, 2553250]
config:            # deep-merged over settings.yaml defaults
  optimization:
    tmax: 3.0
```

For GeoPackage inputs use `gpkg: <path>` with `roads_layer`,
`buildings_layer` and either `sink: [x, y]` or `wwtp_layer: <layer>`.

## Sharing

The repository is public on
[codebase.helmholtz.cloud/wasp/pysewer](https://codebase.helmholtz.cloud/wasp/pysewer)
with a mirror on [GitHub](https://github.com/ddspot/pysewer): pushing to
`main` shares scenarios and scripts with all collaborators. Exchange
result JSONs (small, rounded) via merge requests or attach them to issues —
never commit input datasets.
