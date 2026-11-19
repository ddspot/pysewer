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
- [x] `needs_pump` dropped from the designed network/exports (2026-08-06,
      after the Elan bug report showed it being misread again); it remains
      on the connection graph for routing
- [x] Elan bug report 2026-08-06 addressed (see CHANGELOG Unreleased):
      config validation (negative tmin), needs_pump strip, diameter bug
      verified fixed on current main (their snapshot predates the v0.2.0
      velocity-fallback fix — repro: work/benchmarks/elan/
      repro_feedback_2026-08-06.json), station-count shifts quantified
      (tmax=8: 28 pumping + 35 lifting vs their v1 27+33)
- [ ] Ask Elan which pysewer commit/version their "Pysewer-2" snapshot is —
      confirm the 0.4-diameter run predates the v0.2.0 diameter fix, and
      have them rerun on current main
- [ ] Benchmarks: extend the scenario matrix (max_slope sweep — their
      "to do: play with max_slope" —, other areas, pump_penalty sweep);
      add more beginner notebooks (02: custom settings, 03: reading
      violations)
- [x] **Butler conformity P0 + P1 implemented** (2026-08-06, see
      docs/butler_conformity_assessment.md and CHANGELOG): true d/D
      criterion + reporting; variable peak factor (babbitt/harman/gifft,
      cap 6) + infiltration_fraction; deemed-to-satisfy gradients replace
      velocity_min at intermittent heads. Defaults are backward-compatible
      (constant PF, no infiltration) — **decide whether to switch the
      recommended defaults to babbitt + infiltration 0.1 in the docs/
      example settings** after the next Elan benchmark round
- [x] **Design fill target set to 0.70** (2026-08-08): `max_depth_ratio`
      default lowered 0.75 → 0.70. 0.75 is the Butler/BS EN 752 absolute
      capacity limit (d/D 0.75 ⇔ Q/Q_full 0.912); designing exactly to it
      left trunks at zero reserve (Wasweta II: 29.8 L/s in a DN200 at 1% ⇒
      d/D 0.748). 0.70 tips borderline trunks to the next commercial size.
      Worked calc: benchmarks/scripts/diameter_selection_calc.py; rationale:
      docs/butler_conformity_assessment.md §6. Still open from the same
      round: whether to also default babbitt PF + infiltration 0.1 (decide
      after the next Elan benchmark; note infiltration would push the same
      Wasweta edges to DN300 for the honest reason — more design flow)
- [ ] **Benchmark against external synthetic-network datasets** (user to
      provide): datasets on which other synthetic sewer-generation models
      were trained/evaluated. Add as benchmark cases (same v0120-vs-current
      harness) and compare pysewer's topology, diameters and station counts
      against those references — the acceptance test that our sizing matches
      independent tools, not just our own older version
- [ ] Butler conformity, remaining (optional): Discharge Unit method
      (Q = k·√ΣDU) as head-flow model; Colebrook-White (k_s 0.6/1.5 mm)
      as alternative to Manning; velocity check at 2·DWF per Sewers for
      Adoption
- [ ] **reviewer_full benchmark → EVE (HPC)** (2026-08-08): dropped from the
      local suite — at 7,893 roads / 17,065 buildings BOTH versions get
      OOM-killed on the laptop (SIGKILL after 4.4 h / 6.2 h), so it needs an
      EVE job via `bin/bootstrap_env.sh` + SLURM (high-mem node). While
      there, profile connection-graph memory — the ceiling is shared by
      v0.1.20 and current, so it's a scalability limit, not a regression

## After 0.4.0 (2026-11-19)

The open items above stay open. These items are new.

- [ ] **The pump penalty has almost no effect on the routed network.**
      Results on 0.4.0 with pump penalty 1, 1,000 and 1,000,000:
      - Test data: the network does not change. The road graph has one
        loop. All 7 edges with `needs_pump` are bridges.
      - Arima (603 buildings, 248 loops): 10 of approximately 1,320 pipes
        change. Lifting stations go from 23 to 22. There are no pumping
        stations. Pump penalty 1,000 and 1,000,000 give the same network.

      Cause: the pump penalty applies only to edges with `needs_pump`. An
      edge has `needs_pump` when it cannot drain by gravity from a minimum
      trench. In Arima, 103 of 2,465 edges have `needs_pump`, and 46 of
      them are bridges. With pump penalty 1, the routed network uses only
      5 of them. The design step places the stations. It places a station
      where the trench depth along the route becomes more than `tmax`. The
      routing weight does not include this trench depth.

      The tests in `tests/test_pump_penalty_routing.py` set the weights
      manually. No test shows that the pump penalty changes a real network.

      The response to ELAN (work/2026-08-05_pysewer_response_to_elan.md)
      says that the pump penalty "now actually steers the routing". This
      is correct, but the effect is small. Correct this in the next
      message to ELAN.

      Options, from least work to most work:
      1. Document the limit. The pump penalty applies only to edges with
         `needs_pump`. The design step places the stations from the
         trench depth.
      2. Add the trench depth to the edge weight. For example, add an
         excavation cost for the trench depth that an edge causes
         downstream. This option includes the trench depth only in part.
      3. **Done (!21, 2026-11-19): `pysewer.route_and_design`.** Use a
         loop:
         1. Route the network.
         2. Do the design step.
         3. Increase the weights of the edges near each station.
         4. Route the network again.
         5. Stop when the number of stations does not decrease.
      4. Include the station cost in the routing problem. This option is
         the most correct. It is a research task.

      For each option, add a regression test on a case with loops (for
      example, Arima). In this test, a higher pump penalty must decrease
      the number of stations or the pressurized length. For option 3, the
      test is in `tests/test_route_and_design.py` (example data).

      Related: `needs_pump` on the connection graph used `min_slope`
      (1:100), but the design step uses the self-cleansing slope (1:222 or
      flatter). Thus the pump penalty applied to edges that the design
      step builds as gravity pipes (Chaguanas: 42 of 53 edges). !22 fixes
      this. The fix does not decrease the number of stations.

      Still open:
      1. `route_and_design` is optional. Decide before 0.5 if it becomes
         the default.
      2. Correct the statement about the pump penalty in the next message
         to ELAN.

- [ ] **Issue #4, root cause.** In `connect_to_roadnetwork`, connect the
      building to the existing vertex. Do not split the road edge.
      This change is on hold. The building becomes a node with two edges,
      and `simplify_graph` removes these nodes. On the test data, the
      number of connected buildings goes from 57 to 45. Before the change:
      1. Get the dataset of the issue reporter.
      2. Make sure that each removed building keeps its house connection
         and its population.

      The geometry fix (`dedupe_coords`) is in 0.3.0.

- [ ] **reviewer_full: run on the laptop first.** In 0.4.0, `rsph_tree`
      does not calculate all-pairs shortest paths. On Chaguanas, the
      memory goes from 14.3 GB to 0.3 GB. Run the case on the laptop
      again. If it fails, use the EVE job.

- [ ] **SteinerPy (ddspot/pysewer#12).** Berend Markhorst offers SteinerPy
      as an optional routing backend. SteinerPy solves the directed
      Steiner tree problem to the optimum.
      - First result on Arima: SteinerPy finds the optimum in 39 s. The
        RSPH cost is 0.8 % more than the optimum. With the optimum
        network, the design step places 19 lifting stations, not 22.
      - `benchmarks/scripts/export_steiner_instances.py` writes the seven
        benchmark cases as JSON instances. The instances are in
        work/benchmarks/pysewer_steiner_instances.zip.
      - Do not attach the instances to the public issue. Send them by
        email. Some data is not cleared for public release.

      Next steps:
      1. Send the reply on the issue. (Done 2026-11-19.)
      2. Send the instances by email. (Done 2026-11-19.)
      3. Compare RSPH with SteinerPy on all seven cases. Compare the cost
         and the design result (stations, pressurized length).
      4. If the difference is large, add SteinerPy as an optional backend
         (`pysewer[steiner]`).
      5. Share the Amman case when the stormwater repository is public.
