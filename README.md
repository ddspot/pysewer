<!-- SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
SPDX-License-Identifier: GPL-3.0-only -->

# pysewer

- [pysewer](#pysewer)
  - [Summary](#summary)
  - [Documentation](#documentation)
  - [Installation](#installation)
    - [Step 1: Clone the repository and navigate to the root directory](#step-1-clone-the-repository-and-navigate-to-the-root-directory)
    - [Step 2: Create the conda environment (native layer)](#step-2-create-the-conda-environment-native-layer)
    - [Step 3: Install pysewer with uv (PyPI layer)](#step-3-install-pysewer-with-uv-pypi-layer)
  - [Input Data and data representation](#input-data-and-data-representation)
    - [Preprocessing](#preprocessing)
    - [Graph Attributes](#graph-attributes)
  - [Routing Solver](#routing-solver)
    - [Pump penalty demonstration](#pump-penalty-demonstration)
  - [Plotting](#plotting)
  - [Export](#export)
  - [Default parameters](#default-parameters)
  - [License](#license)
- [How to contribute to pysewer?](#how-to-contribute-to-pysewer)
  - [Code of conduct](#code-of-conduct)
  - [How to cite?](#how-to-cite)

<!-- /TOC -->

## Summary

![Example of an automatically generated Sewer Network](notebooks/example_data/plots/modeldomain_pumps.png)

The aim of pysewer is to provide a framework automatically generate cost-efficient sewer network layouts on minimal data requirements.

It is build around an algorithm for generation of viable sewer-network layouts. The approximated sewer network is represented by sources (households/buildings), potential pathways, and one or multiple sinks. The algorithm approximates the directed steinertree (the steiner arborescence) between all sources and the sink by using an repeated shortest path heuristic (RSPH).

## Documentation

The documentation can be found at [ddspot.github.io/pysewer](https://ddspot.github.io/pysewer/)
(built by the codebase.helmholtz.cloud CI and served statically via the
[GitHub mirror](https://github.com/ddspot/pysewer); Helmholtz users can also
use the [internal Pages](https://wasp.pages.hzdr.de/pysewer/), which requires
a Helmholtz AAI login).

An example of how to use pysewer for generating a sewer network layout can be found here: [example_sewer_network_generation](notebooks/example_sewer_network_generation.ipynb).

## Installation

pysewer uses a **two-layer environment**: the geospatial C-library stack
(GDAL, PROJ, GEOS, rasterio, fiona, geopandas, shapely, …) is installed with
conda/[mamba](https://mamba.readthedocs.io) from [environment.yml](environment.yml),
and the pure-Python layer (plus the editable install of pysewer itself) with
[uv](https://docs.astral.sh/uv/) from [pyproject.toml](pyproject.toml).
Never install the geospatial C-libraries with pip.

### Step 1: Clone the repository and navigate to the root directory

```shell
git clone https://codebase.helmholtz.cloud/wasp/pysewer.git
cd pysewer
```

### Step 2: Create the conda environment (native layer)

```shell
mamba env create -f environment.yml   # creates the "pysewer" env
```

For reproducible builds, `conda-lock.yml` pins the exact conda layer
(regenerate with `make lock`).

### Step 3: Install pysewer with uv (PyPI layer)

```shell
uv pip install --python "$(conda info --base)/envs/pysewer/bin/python" -e '.[dev]'
```

Alternatively, `make env-local` (see `mk/env.mk`) performs both steps, and on
HPC/SLURM systems use `source bin/bootstrap_env.sh pysewer` which creates the
env under `/work/$USER/conda_envs` and runs the uv step.

#### Light install (without plotting)

matplotlib is an optional dependency: `import pysewer` and the full
preprocessing → routing → design → export chain work without it, which keeps
installs small when pysewer is embedded in another tool (e.g. the
[Elan](https://elan-gis.org) QGIS plugin, where QGIS already ships
matplotlib). The plotting module is loaded lazily on first use; to enable it,
install the `plot` extra:

```shell
uv pip install -e '.[plot]'      # or: pip install 'pysewer[plot]'
```

Calling a plotting function without matplotlib raises an `ImportError` that
points to this extra. The conda environment above already includes
matplotlib, so nothing extra is needed in the standard setup.

Please see the [documentation](https://ddspot.github.io/pysewer/) for more details.

## Input Data and data representation

The following input data is required:

- A Digital Elevation Model (DEM)
- Point Data on Building locations
- Road Network Data

### Preprocessing

The main objective of sewer layout generation is to connect all buildings to a waste water treatment plant (WWTP) while keeping system cost low. The initial graph represents all potential sewer lines in our model domain.

Preprocessing comes down to:

- "connecting" buildings to the street network
- clustering of buildings surpassing a predefined threshold
- contracting the street network for more efficient graph traversal

After preprocessing, all relevant data is and stored as a MultiDiGraph to allow for asymmetric edge values (e.g. elevation profile and subsequently costs).

### Graph Attributes

```yaml
Node Attributes:
    "node_type": "building","wwtp"
    "elevation"
    "pumping_station": bool
    "lifting_station":bool
Edge Attributes:
    "geometry": detailed shapely line
    "length"
    "diameter"
    "pressurized": bool   # authoritative design flag (pump upstream)
    "profile"
    "private_sewer":bool
    "weight": value representing arbitrary cost function
```

The connection graph additionally carries a `needs_pump` flag per candidate
edge — a terrain-feasibility input used to weight routing (pump penalty).
It is stripped from the final designed network: there, `pressurized` (plus
the `pumping_station`/`lifting_station` node flags) is the design result.

## Routing Solver

![Routing Animation](/notebooks/example_data/plots/rsph.gif)

The package comes with two solvers to find estimates for the underlying steiner tree problem (more specifically minimum steiner arboresence).

- RSPH
- RSPH Fast

---

The _RSPH solver_ iteratively connects the nearest unconnected node (in terms of distance and pump penalty) to the closest connected network node. The solver can account for multiple sinks and is therefore well suited to generate decentralized network scenarios.

The _RSPH Fast_ solver derives the network by combining all shortest paths to a single sink. Faster, but only allows for a single sink.

### Pump penalty demonstration

To validate the effect of the pump penalty logic we provide a synthetic regression in `test_scripts/demo_pump_penalty_effect.py`.  
Running

```shell
python3 test_scripts/demo_pump_penalty_effect.py
```

creates a small toy network in which a pumped shortcut competes with a gravity detour.  
After the penalty escalation rerun, the solver switches to the gravity route and the pump count drops from two edges to zero.

![Pump penalty demo](docs/pump_penalty_demo.svg)

The script also exports `docs/pump_penalty_demo.png` (when `matplotlib` is available) so the figure can be regenerated from source.

### Hydraulic constraints & validation

Recent updates added geometric and hydraulic checks to reduce unrealistic layouts:

- **Geometry (connection graph):** cover must stay above `min_cover` (default 0.25 m); short edges are flagged if < `min_pipe_length` (default 2 m); excessively steep slopes are flagged (`max_slope`). Cover violations are recorded per edge but do not force a pump — burial depth is governed by `tmin`.
- **Hydraulics (sizing):** gravity pipes are picked to satisfy d/D ≤ `max_depth_ratio` (default 0.75) and velocities within [`velocity_min`, `velocity_max`] (defaults 0.7–3 m/s). Violations are recorded on edges (`hydraulic_violations`), along with computed `velocity` and `d_over_D`.
- **Inspection:** the example notebook now includes cells that summarize pump/constraint flags on the connection graph and hydraulic violations after sizing.

Profile smoothing is not applied; the checks run on the sampled profile (spacing `dx`). Increase `dx` if you want a smoother profile check.

## Plotting

Plotting requires matplotlib (included in the conda environment; on a light
install add it via `pip install 'pysewer[plot]'`). The module is loaded
lazily on first use.

```python
info = pysewer.get_sewer_info(G)
info["Routing Solver"] = "RSPH"
info["Pump Penalty"] = test_model_domain.pump_penalty
fig,ax = pysewer.plot_model_domain(test_model_domain, plot_sewer=True,sewer_graph = G, info_table=info)
```

```python
pysewer.plot_sewer_attributes(test_model_domain,G,attribute="peak_flow",title="Peak Flow Estimation m³/s")
plt.show()
```

## Export

```python
sewer_network_gdf = pysewer.get_edge_gdf(G,detailed=True)
pysewer.export_sewer_network(sewer_network_gdf, "sewer_network.gpkg")
```

Supported formats are GeoPackage (`gpkg`, default), ESRI Shapefile (`shp`)
and GeoParquet (`parquet`). Multi-layer GeoPackages with an explicit layer
name and CRS can be written with `pysewer.write_gdf_to_gpkg(gdf, path,
layer=..., crs=...)`.

Float attributes are rounded on export to keep the files free of floating
point noise, controlled by `export.round_decimals` in the settings
(`default: 3` decimal places for all float columns and profile tuples,
`peak_flow: 6` since it is in m³/s, `slope: 5`; set to `null` to disable
and export raw values).

## Default parameters

The default or global parameters are stored in the [settings.yaml](pysewer/config/settings.yaml) file. It can be overridden with a custom settings file (e.g. [example_settings.yaml](notebooks/example_settings.yaml)) via `pysewer.set_custom_config(custom_path=...)` (or a dict via `custom_settings_dict=...`). The settings are categorized into 4 sections, namely `preprocessing`, `optimization`, `plotting` and `export`.

**Set the custom config before creating the `ModelDomain`.** The
`preprocessing` parameters `clustering` and `connect_buildings` are consumed
while the connection graph is built inside `ModelDomain(...)`, so config
overrides applied afterwards cannot affect them:

```python
pysewer.set_custom_config(custom_path="my_settings.yaml")  # 1st
md = pysewer.ModelDomain(dem, roads, buildings)            # 2nd
```

Most other parameters (including `pump_penalty` and all `optimization`
values) are read from the live config at the pipeline stage that uses them,
so they may also be changed between stages.

The table below summaries the key default parameters and their meaning.

| Parameter                 | Description                                                                                                      | Default |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------- |
| `dem_file_path`           | Path for the DEM file                                                                                            | None    |
| `roads_input_data`        | Path for the road input data                                                                                     | None    |
| `buildings_input_data`    | Path for the buildings input data                                                                                | None    |
| `pump_penalty`            | Penalty for using a pump in the cost function                                                                    | 1000    |
| `dx`                      | Sampling resolution, used for extracting elevation data from the DEM (in meters)                                 | 10      |
| `max_connection_length`   | The maximum distance between a building and the nearest street for it to be included in the cluster centers list | 30      |
| `inhabitants_dwelling`    | The number of inhabitants per dwelling                                                                           | 2       |
| `inhabitants_dwelling_attribute_name` | Buildings attribute holding the inhabitants count (overrides `inhabitants_dwelling` if set)          | ""      |
| `daily_wastewater_person` | The daily wastewater generated per person in m³                                                                  | 0.15    |
| `peak_factor`             | Peak factor for wastewater                                                                                       | 4.0     |
| `min_slope`               | Minimum slope required for gravity flow (negative value = downhill)                                              | -0.01   |
| `max_slope`               | Steepest allowed pipe slope before flagging                                                                      | -0.05   |
| `tmax` / `tmin`           | Maximum / minimum trench depth (m)                                                                               | 6.0 / 0.25 |
| `inflow_trench_depth` / `min_trench_depth` | Trench depth at the inflow point / lowest possible trench depth (m)                             | 0.25 / 0.25 |
| `min_cover`               | Minimum cover depth over pipe (m); must not exceed `tmin`                                                        | 0.25    |
| `min_pipe_length`         | Shortest segment length before flagging (m)                                                                      | 2.0     |
| `velocity_min`/`velocity_max` | Bounds on design velocity (m/s)                                                                              | 0.7 / 3.0 |
| `max_depth_ratio`         | Maximum d/D used when sizing gravity pipes                                                                       | 0.75    |
| `diameters`               | List of diameters to be considered (meters)                                                                      | 0.2 … 2.0 |
| `pressurized_diameter`    | Diameter of pressure pipes to be used (meters)                                                                   | 0.3     |
| `roughness`               | The pipe roughness coefficient in meters                                                                         | 0.0015  |
| `round_decimals`          | Decimal places for float columns in exported files (per-column map, `null` disables)                             | default 3, `peak_flow` 6, `slope` 5 |

## License

GNU GPLv3-modified-UFZ. See [LICENSE](LICENSE) for details.

# How to contribute to pysewer?

Please check out how [Contributing](CONTRIBUTING.md) for on how to contribute to pysewer. Please note that we have created a mirror repository on [Github](https://github.com/ddspot/pysewer) to allow for easier contribution. The original repository is hosted on [Gitlab](https://codebase.helmholtz.cloud/wasp/pysewer).

## Code of conduct

Please check out our [Code of Conduct](CODE_OF_CONDUCT.md) for details.

## How to cite?

[![DOI](https://joss.theoj.org/papers/10.21105/joss.06430/status.svg)](https://doi.org/10.21105/joss.06430)

pysewer is published in the Journal of Open Source Software (JOSS). If you use
pysewer in your work, please cite (see also [CITATION.cff](CITATION.cff)):

> Sanne, M., Khurelbaatar, G., Despot, D., van Afferden, M., & Friesen, J.
> (2024). Pysewer: A Python Library for Sewer Network Generation in Data
> Scarce Regions. *Journal of Open Source Software*, 9(104), 6430.
> https://doi.org/10.21105/joss.06430

```bibtex
@article{Sanne2024pysewer,
  author  = {Sanne, Moritz and Khurelbaatar, Ganbaatar and Despot, Daneish
             and van Afferden, Manfred and Friesen, Jan},
  title   = {Pysewer: A Python Library for Sewer Network Generation
             in Data Scarce Regions},
  journal = {Journal of Open Source Software},
  year    = {2024},
  volume  = {9},
  number  = {104},
  pages   = {6430},
  doi     = {10.21105/joss.06430},
}
```
