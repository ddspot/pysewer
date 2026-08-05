# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

try:
    from osgeo import ogr
except ImportError as e:
    raise Exception(""" ERROR: Could not find the GDAL/OGR Python library bindings. 
               On Debian based systems you can install it with this command:
               apt install python-gdal""") from e

# Importing everything allows to use "import pysewer" and then access all functions on the same level e.g. pysewer.ModelDomain()
from .config.manager import get_config, set_config
from .export import *
from .helper import *
from .optimization import *
from .preprocessing import *
from .routing import *


def __getattr__(name):
    # Plotting (and with it matplotlib) loads lazily on first access, so
    # "import pysewer" works in plot-free environments (e.g. the Elan QGIS
    # plugin) without matplotlib installed.
    if name.startswith("_"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    plotting = import_module(".plotting", __name__)
    if name == "plotting":
        return plotting
    try:
        return getattr(plotting, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None

# Load default settings on package import
DEFAULT_CONFIG = get_config()


def set_custom_config(custom_path=None, custom_settings_dict=None):
    """
    Override the package-wide configuration.
    """
    global DEFAULT_CONFIG
    DEFAULT_CONFIG = set_config(custom_path, custom_settings_dict)
