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
- [x] **Public docs hosting resolved**: codebase.helmholtz.cloud forces
      Helmholtz AAI login for ALL Pages sites (instance policy, re-verified
      2026-07-27: every *.pages.hzdr.de site 302s to the AAI sign-in), so
      public docs live at **https://ddspot.github.io/pysewer/**. To conserve
      GitHub Actions minutes the docs are NOT built with Actions: the
      codebase CI `docs-mirror` job pushes the sphinx build to the mirror's
      `gh-pages` branch and GitHub Pages serves it statically ("deploy from
      branch"). Helmholtz-internal copy: https://wasp.pages.hzdr.de/pysewer/
      (AAI login).
- [x] **`GITHUB_MIRROR_TOKEN` CI/CD variable added on codebase** (2026-07-27):
      fine-grained GitHub PAT, contents:read+write on ddspot/pysewer; the
      `docs-mirror` job now publishes gh-pages automatically on every push
      to main (`make docs-publish` remains as manual fallback).
- [ ] Decide the fate of the old git.ufz.de/despot/pysewer repo
      (currently untouched as a safety net): archive + "moved" notice?
- [x] GitHub mirror repointed (account renamed dbdespot → ddspot):
      main @ v0.2.0 + tags pushed, test workflow modernized (micromamba+uv),
      obsolete JOSS draft-paper workflow removed. Consider configuring
      automatic push-mirroring on codebase (Settings → Repository →
      Mirroring) so future pushes sync without manual `git push github`
- [ ] Notify Jacky Volpes / ELAN that their contributions are merged
      (draft message in session transcript; their fork can retarget)
- [ ] **min_cover design question**: cover violations no longer force pumps
      and min_cover defaults to tmin (0.25 m). If a real burial requirement
      is wanted, model it properly (e.g. effective tmin = min_cover + pipe
      diameter) instead of a flat flag
- [ ] Velocity-min violations are now recorded honestly (partial-flow
      Manning velocity); many rural edges will legitimately carry the flag.
      Consider a flush-interval/self-cleansing note in docs instead of
      treating it as a design failure
- [x] `mean_td` tuple-concatenation expression fixed (5b1fbfc)
- [x] Ruff: 159 → 28 findings (two fix passes + config in pyproject.toml +
      non-blocking lint CI job); remaining 28 are judgment calls
      (bare-excepts, mutable default args, zip strict=)
- [x] earthpy dependency dropped — hillshade vendored in plotting.py
      (10-line ESRI formula), conda locks regenerated (5b1fbfc)
- [ ] Stale branches on the new remote — all three are safe to delete
      pending your OK (verified 2026-07-24): **combined-sewers** is fully
      superseded (combined_sewer_factor, empty-gdf checks and geometry
      validation are all in main already); **sphinx-docs** and
      **base-ci-pipeline** are superseded by the consolidated CI
- [x] install.rst rewritten for the two-layer workflow (7ebe6e0);
      remaining: api_reference codeautolink warnings
- [x] v0.2.0 tagged and pushed (tag pipeline green); remaining: new Zenodo
      version + archive badge in README if wanted
- [ ] Benchmark follow-up with ELAN colleagues: confirm the custom settings
      used for their reference run (diameter list incl. 0.1/0.15 m,
      inhabitants=3/dwelling, tmax) and re-run the comparison with matched
      settings
- [x] ELAN meeting (2026-06-08) suggestions implemented (2026-08-05):
      export rounding (`export.round_decimals`) and light import (lazy
      plotting, matplotlib → `plot` extra); response draft in
      work/ (2026-08-05_pysewer_response_to_elan.md)
- [ ] Plugin architecture (pluggy, per Jacky's suggestion) for a proper
      light/full split and v1/v2 behavior selection — next branch;
      candidate seams: plotting, exporter backends, constraint checks
- [ ] Consider dropping `needs_pump` from exported layers (routing-internal
      flag, confuses users — Elan read it as the pump indicator; the
      authoritative design flag is `pressurized`). Discuss with ELAN first
