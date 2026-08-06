<!-- SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
SPDX-License-Identifier: GPL-3.0-only -->
<!--
A pysewer result depends on the input data, the configuration AND the exact
code version. Please fill in every section — we can only fix bugs we can
reproduce. See CONTRIBUTING.md > "Reporting bugs".
External collaborators without a Helmholtz account can also file on the
GitHub mirror: https://github.com/ddspot/pysewer/issues
-->

### Summary
<!-- What went wrong, in one or two sentences. -->

### pysewer version / commit
<!-- python -c "import importlib.metadata; print(importlib.metadata.version('pysewer'))"
     and `git rev-parse --short HEAD` if from source. Name any branch/fork. -->

### Minimal reproducer
<!-- Best: a benchmark scenario YAML (benchmarks/README.md) so we run exactly
     what you ran. Otherwise a short self-contained Python script. -->
```python
```

### Configuration (non-default parameters)
<!-- Your set_custom_config(...) dict or custom settings.yaml. List every
     changed parameter. Set custom config BEFORE constructing ModelDomain. -->
```yaml
```

### Input data
<!-- Extent, CRS, #buildings/roads, and how to get it. If it can't be shared
     publicly, say so — we'll arrange a private transfer. A clipped/synthetic
     extract that still shows the bug is very welcome. -->

### Expected vs. actual behavior
<!-- With numbers where relevant: diameter distribution, station counts,
     an attribute table, a plot. -->

### Environment
<!-- python -c "import geopandas, shapely, numpy, pysewer; print(geopandas.__version__, shapely.__version__, numpy.__version__)"
     plus OS and Python version. -->

/label ~bug
