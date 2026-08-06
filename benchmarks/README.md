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

## Worked example — diameter sensitivity

Why does pysewer put almost every pipe at the smallest available diameter?
The `diamsweep_*` scenarios answer this. They run the **same** repo test
network and change **only** the available diameter list:

```shell
python benchmarks/scripts/run_scenarios.py \
    diamsweep_coarse diamsweep_default diamsweep_probe
```

| scenario (floor) | max fill | gravity diameters | note |
|---|---|---|---|
| diamsweep_default (0.2 m / DN200) | 0.019 | 0.2: 84, 0.3: 11 | realistic minimum |
| diamsweep_coarse (0.3 m / DN300) | 0.006 | 0.3: 95 | coarser realistic set |
| diamsweep_probe (0.05 m) | 0.70 | 0.05: 83, 0.08: 1, 0.1: 11 | **diagnostic only — non-physical sizes** |

`max fill` = the largest peak-flow / full-bore-capacity ratio across gravity
pipes.

**Engineering conclusion (the headline).** Public gravity sewers have a
*minimum diameter* — typically DN150–DN200 — set by maintenance, blockage
and self-cleansing rules, **not** by peak flow. On a small domestic
catchment like this one the peak flows are far too small to require anything
above that minimum (`max fill` ~2 % at DN200), so the correct design is the
minimum diameter almost everywhere, stepping up only on the trunk. That is
exactly what pysewer produces — and it is what a design engineer would
specify. "All at minimum" is the right answer here, not a symptom of broken
sizing.

**Why the probe row exists.** With realistic diameters the capacity
constraint never binds, so a natural question is whether the sizing engine
*can* escalate at all, or is stuck at the floor. `diamsweep_probe` answers
that by offering deliberately **non-physical** sub-sewer sizes (0.05/0.08 m
are house-lateral diameters, not public sewers). Only once the smallest pipe
is that small does the trunk reach capacity (`max fill` 0.70, right at the
0.75 `max_depth_ratio`) and the engine correctly steps the diameter up,
producing a spread (0.05 → 0.08 → 0.1 m). So the engine is verified to size
by capacity when capacity matters; it simply never matters at realistic
sewer diameters on catchments this small. **Do not read the probe diameters
as recommended pipe sizes.**

Takeaway: the sizing is **floor-limited, not capacity-limited** here —
diameters are small because the flows are small, and the smallest *realistic*
diameter is the correct engineering answer. A finer spread would require
either genuinely larger design flows or sub-minimum pipes that no one would
lay.

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
