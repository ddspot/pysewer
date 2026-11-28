.. SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
.. SPDX-License-Identifier: GPL-3.0-only

Installation
============

pysewer uses a **two-layer environment**:

1. **Native layer (conda/mamba)** — the geospatial C-library stack (GDAL,
   PROJ, GEOS, rasterio, fiona, geopandas, shapely, …) from
   ``environment.yml``. Never install these with pip.
2. **PyPI layer (uv)** — pure-Python dependencies and the editable install
   of pysewer itself from ``pyproject.toml``.

We recommend `Miniforge <https://github.com/conda-forge/miniforge>`_ (which
ships mamba) and `uv <https://docs.astral.sh/uv/>`_.

Step 1: Clone the repository
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

    git clone https://codebase.helmholtz.cloud/wasp/pysewer.git
    cd pysewer

Step 2: Create the conda environment (native layer)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

    mamba env create -f environment.yml    # creates the "pysewer" env

For fully reproducible builds, ``conda-lock.yml`` pins the exact conda
layer (regenerate with ``make lock``).

Step 3: Install pysewer with uv (PyPI layer)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

    uv pip install --python "$(conda info --base)/envs/pysewer/bin/python" -e '.[dev]'

Alternatively, ``make env-local`` performs both steps (see ``mk/env.mk``).

HPC / SLURM systems
^^^^^^^^^^^^^^^^^^^

On HPC systems, source the bootstrap script, which creates the environment
under ``/work/$USER/conda_envs`` (prefers the lock file) and runs the uv
step:

.. code-block:: bash

    source bin/bootstrap_env.sh pysewer

Verify the installation
^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

    make doctor                       # env layering and import checks
    python -m pytest tests/ -q        # run the test suite
