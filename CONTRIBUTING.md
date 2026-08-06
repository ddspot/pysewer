# Contributing to pysewer

👍🎉 First off, thanks for taking the time to contribute! 🎉👍

These are guidelines, not hard rules — use your best judgment and feel free
to propose changes to this document itself.

## Where pysewer lives

- **Canonical repository (GitLab):**
  <https://codebase.helmholtz.cloud/wasp/pysewer> — CI, releases and the
  authoritative history live here.
- **GitHub mirror:** <https://github.com/ddspot/pysewer> — kept in sync for
  external collaborators who prefer GitHub (and where filing issues does not
  require a Helmholtz account).

You can report issues and open contributions on **either** platform. If you
are an external collaborator without a Helmholtz login, the **GitHub mirror
is the easiest entry point** — we ferry issues/PRs to the canonical repo.

## Code of Conduct

This project is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). By
participating you are expected to uphold it; please report unacceptable
behavior to the contact listed there.

## Reporting bugs — please make it reproducible

**This is the most important section.** A pysewer network depends on the
input data, the configuration, *and* the exact code version. We can only
confirm and fix a bug we can reproduce, so a good report includes all three.
Please use the bug-report template (it appears automatically when you open a
new issue on either platform) and fill in:

1. **pysewer version and commit.** Output of:
   ```python
   import importlib.metadata, subprocess
   print(importlib.metadata.version("pysewer"))
   print(subprocess.run(["git","-C","<repo>","rev-parse","--short","HEAD"],
                         capture_output=True, text=True).stdout)
   ```
   Version provenance matters: several past "regressions" turned out to be
   old snapshots. If you installed from a branch or fork, say which.
2. **A minimal reproducer.** The gold standard is a **benchmark scenario**:
   add a small YAML under `benchmarks/scenarios/` (see
   [`benchmarks/README.md`](benchmarks/README.md)) that declares the dataset
   and the config overrides, so we run *exactly* what you ran with
   `python benchmarks/scripts/run_scenarios.py <name>`. If a scenario is not
   practical, a short self-contained Python script is fine.
3. **The exact configuration.** Your `set_custom_config(...)` dict or custom
   `settings.yaml` — every non-default parameter (`tmax`, diameter list,
   `pump_penalty`, etc.). Remember to set custom config **before**
   constructing the `ModelDomain` (see the config docs).
4. **The input data**, or enough to reproduce it. If the data cannot be
   shared publicly, say so — we will arrange a private transfer, and a
   synthetic or clipped extract that still shows the bug is very welcome.
5. **Expected vs. actual behavior**, with numbers/screenshots where relevant
   (e.g. diameter distribution, station counts, an attribute table).
6. **Environment:** OS, Python version, and key package versions. A quick
   dump: `python -c "import geopandas, shapely, numpy, pysewer; \
   print(geopandas.__version__, shapely.__version__, numpy.__version__)"`.

Before filing, please search existing issues to avoid duplicates.

## Suggesting enhancements

Open an issue with the feature template. Describe the use case (the *why*),
not only the *what*, and give a concrete example if you can.

## Contributing code (merge/pull requests)

Only maintainers commit directly to `main`. All contributions go through a
merge request (GitLab) or pull request (GitHub) from a branch or fork:

1. Fork or branch from the current `main`.
2. Make focused changes on a topic branch.
3. **Add or update tests** (pytest) for any new or changed behavior, and run
   the suite locally: `python -m pytest tests/ -q`.
4. Keep the changelog current (`CHANGELOG.md`, "Unreleased" section) and the
   docs/README if you changed user-facing behavior.
5. Run the linter: `ruff check pysewer/ tests/`.
6. Open the MR/PR against `main`. It will be reviewed before merging.

Notes:
- `main` is protected; the CI (pytest + docs build) must pass.
- Avoid breaking changes unless discussed with the maintainers first.
- External contributions preserve your authorship when we ferry them between
  the mirror and the canonical repo.

## Style

### Commit messages
- Imperative present tense ("Add feature", not "Added feature").
- Keep the first line ≤ 72 characters; explain the *why* in the body.

### Issues and PRs
- Clear, descriptive title.
- Enough detail and concrete examples for someone else to act on it.

## Issue labels

- `bug` — something is broken
- `enhancement` — a feature request
- `discussion` — open-ended discussion
- `help wanted` — assistance welcome

## References

- [Open Source Guides](https://opensource.guide/)
- [scipy contributing](https://github.com/scipy/scipy/blob/main/CONTRIBUTING.rst)
- [How to write a reproducible bug report](https://stackoverflow.com/help/minimal-reproducible-example)
