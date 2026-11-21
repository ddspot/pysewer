# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

# Importing everything allows to use "import pysewer" and then access all functions on the same level e.g. pysewer.ModelDomain()
from .config.manager import get_config, set_config
from .export import *  # noqa: F403
from .helper import *  # noqa: F403
from .logs import CapturedLog as CapturedLog
from .logs import capture_log as capture_log
from .logs import show_progress as show_progress
from .optimization import *  # noqa: F403
from .preprocessing import *  # noqa: F403
from .routing import *  # noqa: F403

# Submodules that load lazily on first access. Heavy or optional dependencies
# (matplotlib, osmnx, plotly, ...) stay out of "import pysewer" this way.
_LAZY_SUBMODULES = (
    "viz",
    "bundle",
    "metrics",
    "compare",
    "sources",
    "pipeline",
    "cli",
    "plotting",
)

# Public names of pysewer.plotting, reachable as pysewer.<name>.
_PLOTTING_NAMES = (
    "plot_model_domain",
    "plot_sewer_attributes",
    "plot_connection_graph",
)

# Optional-dependency extra that provides the third-party imports of a submodule.
_LAZY_EXTRAS = {"plotting": "plot", "viz": "plot", "sources": "fetch"}


def _import_lazy(name):
    """
    Import the lazy submodule ``name``.

    Raises ``AttributeError`` if the submodule does not exist (yet) and
    ``ImportError`` with install advice if a third-party dependency is missing.
    """
    from importlib import import_module

    try:
        return import_module(f".{name}", __name__)
    except ModuleNotFoundError as e:
        if e.name == f"{__name__}.{name}":
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r}"
            ) from None
        missing = (e.name or "").split(".")[0]
        if not missing or missing == __name__:
            raise
        extra = _LAZY_EXTRAS.get(name)
        if name == "plotting" and missing in ("matplotlib", "mpl_toolkits"):
            raise ImportError(
                "pysewer's plotting functions require matplotlib, which is "
                "not installed. Install it with: pip install 'pysewer[plot]' "
                "(or add matplotlib to your environment)."
            ) from e
        if extra:
            raise ImportError(
                f"pysewer.{name} requires {missing!r}, which is not installed. "
                f"Install it with: pip install 'pysewer[{extra}]'"
            ) from e
        raise ImportError(
            f"pysewer.{name} requires {missing!r}, which is not installed."
        ) from e


def __getattr__(name):
    # Plotting (and with it matplotlib) loads lazily on first access, so
    # "import pysewer" works in plot-free environments (e.g. the Elan QGIS
    # plugin) without matplotlib installed.
    if name.startswith("_"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name in _LAZY_SUBMODULES:
        module = _import_lazy(name)
        globals()[name] = module  # later access skips __getattr__
        return module
    # Otherwise fall back to the public names of the plotting module. Only
    # known plotting names import it, so tools that probe other names
    # (hasattr(pysewer, "pytestmark")) get an AttributeError, not matplotlib.
    if name not in _PLOTTING_NAMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    plotting = _import_lazy("plotting")
    try:
        return getattr(plotting, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None


def __dir__():
    from importlib.util import find_spec

    lazy = {n for n in _LAZY_SUBMODULES if find_spec(f"{__name__}.{n}") is not None}
    return sorted(set(globals()) | lazy)


# Load default settings on package import
DEFAULT_CONFIG = get_config()


def set_custom_config(custom_path=None, custom_settings_dict=None):
    """
    Override the package-wide configuration.
    """
    global DEFAULT_CONFIG
    DEFAULT_CONFIG = set_config(custom_path, custom_settings_dict)
