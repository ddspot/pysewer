.. SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
.. SPDX-License-Identifier: GPL-3.0-only

Command line
============

The ``pysewer`` command designs sewer networks without Python code. Install
the package (see :doc:`install`) and run ``pysewer --help``. Each subcommand
has its own ``--help``.

.. code-block:: text

    pysewer run      design a network from a DEM, roads and buildings
    pysewer fetch    fetch roads, buildings and a DEM into the cache
    pysewer report   write an HTML report of one or more bundles
    pysewer compare  compare two or more bundles
    pysewer profile  show the longitudinal profile from a node to its sink
    pysewer --version

``pysewer run``
---------------

Give either three files or a bounding box.

.. code-block:: bash

    # from files (the DEM CRS must be projected; the sink is in that CRS)
    pysewer run --dem dem.tif --roads roads.gpkg --buildings buildings.gpkg \
        --sink 691350,2553250 --out net.gpkg --report net.html

    # from a bounding box (W,S,E,N in WGS84); needs the "fetch" extra
    pysewer run --bbox 12.30,51.31,12.31,51.315 --sink lowest --out leipzig.gpkg

Inputs
    ``--dem``, ``--roads``, ``--buildings``, or ``--bbox W,S,E,N`` with
    ``--dem-source copernicus|PATH``, ``--crs EPSG``, ``--cache-dir DIR``,
    ``--max-area KM2`` (default 4) and ``--offline`` (cached data only), ``--retries N`` (default 1) and
    ``--retry-wait S`` (default 5). The low defaults make a dead network fail
    in under a minute. The
    Copernicus GLO-30 DEM is a surface model: buildings and trees raise the
    ground. Use a local bare-earth DEM with ``--dem-source PATH`` when you can.

Roads and buildings source
    ``--source osm`` (default) queries an Overpass server. ``--source overture``
    reads the Overture Maps GeoParquet from AWS S3 and needs no Overpass
    server. Overture roads keep ``subtype road`` and the classes primary,
    secondary, tertiary, unclassified, residential, living_street, the
    ``*_link`` classes and service roads (not driveways or parking aisles);
    motorways, trunks and paths are dropped. Overture buildings drop
    underground buildings and footprints under 20 m2. Both sources are ODbL.
    The Overture attribution is "© OpenStreetMap contributors, Overture Maps
    Foundation"; some buildings need an extra note (see
    https://docs.overturemaps.org/attribution/).

Overpass server
    ``--overpass-url URL`` sets the server for ``--source osm``. Without it,
    the command uses the environment variable ``PYSEWER_OVERPASS_URL``, then
    the osmnx default ``https://overpass-api.de/api``. ``.../api`` and
    ``.../api/interpreter`` both work. The network check before the fetch
    probes ``URL/status``. With ``--source overture`` it probes the Overture S3
    bucket instead. The URL does not change the cache key; the provenance
    records it as ``overpass_url``. The cache files of Overture are named
    ``overture_*_<release>_*.gpkg`` and are keyed by box, release and road
    filter; ``--offline`` uses the newest cached release.

Sink
    ``--sink lowest`` (default) uses the lowest road node. ``--sink X,Y`` is a
    location in the DEM CRS. With ``--sink-crs 4326`` it is lon,lat.

Overrides
    ``--tmax M``, ``--tmin M``, ``--people-per-building N``,
    ``--station-cost M``, and any other value with ``--set section.key=value``.
    The value is read as JSON if possible (``3``, ``true``), else as a string.
    ``--set`` wins over the flags. Example:
    ``--set optimization.station_max_iterations=4``. The CLI does not check
    ``--set`` keys against the config schema. pysewer's own validation does it
    when the run starts: an unknown section or an invalid value raises a
    ``ValueError``, which prints an ``error:`` line and exits with code 3. A
    ``--set`` item without ``section.key=value`` form is a usage error (code 2).

Output
    ``--out PATH`` writes the network bundle. ``--format gpkg|geojson|parquet``
    sets the format. Without it, a path ending in ``.gpkg`` is a GeoPackage
    file and any other path is a GeoParquet directory. GeoJSON and Parquet
    bundles are directories. ``--report report.html`` writes the HTML report.
    ``--name`` sets the network name (default: the stem of ``--out``).
    The summary goes to stdout. ``--json`` prints ``network_metrics`` as JSON
    instead.

Progress
    ``--progress text`` (default) writes lines such as ``[load]  25%  ...`` to
    stderr. ``--progress none`` is silent. ``--progress jsonl`` writes one JSON
    object per line to stderr. This format is versioned (``v``); a change that
    breaks it raises ``v``. Fields:

    ``v`` (int)
        Schema version, now ``1``.
    ``phase`` (string)
        ``"fetch"`` for the stages ``fetch:*``, else ``"generate"``.
    ``stage`` (string)
        ``fetch:roads``, ``fetch:buildings``, ``fetch:dem``, ``fetch:done``
        (only with ``--bbox``), then ``load``, ``connect``, ``route_design``
        and ``bundle``. New stages can be added in later versions; no stage is
        removed or renamed inside one ``v``. Ignore stages you do not know.
    ``fraction`` (float)
        0 to 1 over one **phase**, not over the whole run. In the fetch phase:
        ``fetch:roads`` 0 to 0.3, ``fetch:buildings`` 0.3 to 0.6, ``fetch:dem``
        0.6 to 1. The generate phase starts again at 0. Use ``phase`` to see
        the reset.
    ``message`` (string)
        A short text for people.

    Example: ``{"v": 1, "phase": "generate", "stage": "route_design",
    "fraction": 0.62, "message": "..."}``.

Debugging
    ``--debug`` (before or after the subcommand) shows the Python traceback
    of an error instead of one ``error:`` line.

``pysewer fetch``
-----------------

.. code-block:: bash

    pysewer fetch --bbox 12.30,51.31,12.31,51.315 --cache-dir ./cache

Downloads the data into the cache and prints the cache files and the
provenance (sources, licence strings, fetch time). With ``--json`` it prints
one JSON object on stdout and nothing else: ``dem``, ``cache_dir``, ``roads``
and ``buildings`` (counts), ``crs``, ``bbox_wgs84``, ``provenance`` and
``cache_files`` (a list of ``name``, ``path``, ``size_bytes``). The CLI
checks the box and its area before any network access, so a box that is too
large exits with code 4 even when the network is down. Run it once, then use
``--offline`` with the same box and cache folder to work without a network.

``pysewer report``
------------------

.. code-block:: bash

    pysewer report net.gpkg --out report.html
    pysewer report base.gpkg variant.gpkg --out comparison.html --title "Tmax test"

One bundle gives a report. Two or more bundles give a comparison report. The
names come from the bundle metadata, or from the file stem. ``--names A,B``
overrides them.

``pysewer compare``
-------------------

.. code-block:: bash

    pysewer compare base.gpkg variant.gpkg
    pysewer compare base.gpkg variant.gpkg --json > comparison.json

The text lists, for each variant, how much pipe length it shares with the base
network, how much it adds and removes, and which configuration values differ.
``--base NAME`` picks the base (default: the first bundle). ``--json`` prints
the full comparison; add ``--with-geometry`` to include the per-pipe GeoJSON
(``pipe_diff``).

``pysewer profile``
-------------------

.. code-block:: bash

    pysewer profile net.gpkg --node N000115 --json
    pysewer profile net.gpkg --node N000115 --png profile.png

``--png`` needs matplotlib (``pip install 'pysewer[plot]'``).

Exit codes
----------

===== ==========================================================================
Code  Meaning
===== ==========================================================================
0     Success.
1     Unexpected error. A traceback shows only with ``--debug``.
2     Usage or argument error.
3     Input or validation error (a missing file, an invalid value, a failed
      fetch).
4     The bounding box is larger than ``--max-area``.
5     ``--offline`` and the cache has no data for the box.
6     The network is not reachable (Overpass or the DEM server). Check the
      connection or use ``--offline`` with a filled cache.
130   Interrupted with Ctrl-C.
===== ==========================================================================

Every error prints one line that starts with ``error:`` to stderr.
