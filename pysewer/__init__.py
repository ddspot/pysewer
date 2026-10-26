# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

# Importing everything allows to use "import pysewer" and then access all functions on the same level e.g. pysewer.ModelDomain()
from .config.manager import get_config, set_config
from .export import *  # noqa: F403
from .helper import *  # noqa: F403
from .optimization import *  # noqa: F403
from .preprocessing import *  # noqa: F403
from .routing import *  # noqa: F403


def __getattr__(name):
    # Plotting (and with it matplotlib) loads lazily on first access, so
    # "import pysewer" works in plot-free environments (e.g. the Elan QGIS
    # plugin) without matplotlib installed.
    if name.startswith("_"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    try:
        plotting = import_module(".plotting", __name__)
    except ModuleNotFoundError as e:
        if e.name and e.name.split(".")[0] in ("matplotlib", "mpl_toolkits"):
            raise ImportError(
                "pysewer's plotting functions require matplotlib, which is "
                "not installed. Install it with: pip install 'pysewer[plot]' "
                "(or add matplotlib to your environment)."
            ) from e
        raise
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
