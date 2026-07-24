# Post-migration audit & TODO (2026-07-24)

Status after migrating to https://codebase.helmholtz.cloud/wasp/pysewer
(v0.2.0: ELAN contributions + pump-penalty/hydraulic constraints +
uv+mamba two-layer env).

## Verified

- [x] Full history + all branches + 8 tags on codebase.helmholtz.cloud;
      `main` is default; ELAN commits keep Jacky Volpes' authorship
- [x] 43/43 tests pass in the fresh two-layer env
      (py3.11, geopandas 1.1.4, shapely 2.1.2, pandas 3.0.5)
- [x] `make doctor` clean; conda-lock.yml + conda-linux-64.lock committed
- [x] Sphinx docs build in the new env with the `[docs]` extra (warnings only)
- [x] Fresh clone from the new remote verified
- [x] ELAN benchmark (work/benchmarks/elan): identical total_static_head
      extremes, topology within 3%, population attribute drives upstream_pe;
      see work/benchmarks/elan/pysewer_run/benchmark_report.md

## To do

- [x] **CI pipeline green** on codebase.helmholtz.cloud (2026-07-24,
      pipeline 795352: test + pages both pass; make→sphinx fix b207d68)
- [ ] **Make Pages public**: the site is deployed at
      https://wasp.pages.hzdr.de/pysewer/ but redirects to sign-in —
      set Settings → General → Visibility → Pages → Everyone; then update
      the README docs link (currently still https://despot.pages.ufz.de/pysewer)
- [ ] Decide the fate of the old git.ufz.de/despot/pysewer repo
      (currently untouched as a safety net): archive + "moved" notice?
- [ ] Repoint the GitHub mirror (github.com/dbdespot/pysewer) to mirror the
      new remote; notify Jacky Volpes / ELAN that their contributions are
      merged (their fork can also retarget)
- [ ] **min_cover design question**: cover violations no longer force pumps
      and min_cover defaults to tmin (0.25 m). If a real burial requirement
      is wanted, model it properly (e.g. effective tmin = min_cover + pipe
      diameter) instead of a flat flag
- [ ] Velocity-min violations are now recorded honestly (partial-flow
      Manning velocity); many rural edges will legitimately carry the flag.
      Consider a flush-interval/self-cleansing note in docs instead of
      treating it as a design failure
- [ ] Cosmetic: `mean_td` for pressurized edges uses
      `[profile[0] + profile[-1]]` (tuple concatenation) — result is correct
      but the expression should be `[profile[0], profile[-1]]`
      (pysewer/optimization.py, ELAN commit 0360d99)
- [ ] Ruff baseline: 159 findings (94 auto-fixable) — run
      `ruff check --fix` + manual pass, then add ruff config to
      pyproject.toml and a lint CI job
- [ ] Deprecation sweep: earthpy pulls pkg_resources (dead upstream?);
      consider dropping the earthpy dependency (only used for plotting
      hillshade?)
- [ ] Stale branches on the new remote: combined-sewers (+3 on old main —
      rebase or merge onto v0.2.0), sphinx-docs, base-ci-pipeline
      (superseded by consolidated CI) — merge or delete
- [ ] docs content pass: install.rst still describes the old conda+pip
      flow; api_reference warnings (codeautolink mismatches)
- [ ] JOSS/Zenodo: add archive badge to README; consider tagging v0.2.0
      and minting a new Zenodo version
- [ ] Benchmark follow-up with ELAN colleagues: confirm the custom settings
      used for their reference run (diameter list incl. 0.1/0.15 m,
      inhabitants=3/dwelling, tmax) and re-run the comparison with matched
      settings
