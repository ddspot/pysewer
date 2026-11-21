# SPDX-License-Identifier: GPL-3.0-only
"""Lazy submodule loading in ``pysewer/__init__.py``."""

import importlib
import importlib.util
import subprocess
import sys
import textwrap

import pytest

import pysewer


def _run(code):
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=300,
    )


def _missing(name):
    return importlib.util.find_spec(f"pysewer.{name}") is None


def test_import_pysewer_is_light():
    r = _run(
        """
        import sys
        import pysewer
        heavy = [m for m in ("matplotlib", "mpl_toolkits", "osmnx", "plotly")
                 if m in sys.modules]
        assert not heavy, heavy
        assert "pysewer.plotting" not in sys.modules
        """
    )
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("name", ["bundle", "compare"])
def test_not_yet_existing_submodule_is_absent(name):
    if not _missing(name):
        pytest.skip(f"pysewer.{name} exists now")
    r = _run(
        f"""
        import pysewer
        assert hasattr(pysewer, {name!r}) is False
        try:
            pysewer.{name}
        except AttributeError:
            pass
        else:
            raise SystemExit("no AttributeError")
        """
    )
    assert r.returncode == 0, r.stderr


def test_plotting_access_returns_module_and_caches():
    pysewer.__dict__.pop("plotting", None)
    mod = pysewer.plotting
    assert mod is importlib.import_module("pysewer.plotting")
    assert pysewer.__dict__["plotting"] is mod
    assert pysewer.plotting is mod


def test_plot_function_fallback_resolves():
    import pysewer.plotting as plotting

    assert pysewer.plot_model_domain is plotting.plot_model_domain


def test_unknown_public_name_raises():
    with pytest.raises(AttributeError, match="no_such_name_xyz"):
        _ = pysewer.no_such_name_xyz
    assert not hasattr(pysewer, "no_such_name_xyz")


def test_private_name_does_not_import_plotting():
    r = _run(
        """
        import sys
        import pysewer
        assert hasattr(pysewer, "_nope") is False
        assert "matplotlib" not in sys.modules
        assert "pysewer.plotting" not in sys.modules
        """
    )
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("attr", ["plotting", "plot_model_domain"])
def test_missing_matplotlib_gives_friendly_error(attr):
    r = _run(
        f"""
        import sys
        sys.modules["matplotlib"] = None
        sys.modules["mpl_toolkits"] = None
        import pysewer
        try:
            getattr(pysewer, {attr!r})
        except ImportError as e:
            assert "pysewer[plot]" in str(e), str(e)
            assert not isinstance(e, AttributeError)
        else:
            raise SystemExit("no ImportError")
        """
    )
    assert r.returncode == 0, r.stdout + r.stderr


def _patch_import(monkeypatch, exc):
    def fake(name, package=None):
        raise exc

    monkeypatch.setattr(importlib, "import_module", fake)


def test_import_lazy_missing_extra_dependency(monkeypatch):
    _patch_import(monkeypatch, ModuleNotFoundError("No module named 'osmnx'", name="osmnx"))
    with pytest.raises(ImportError, match=r"pysewer\[fetch\]") as ei:
        pysewer._import_lazy("sources")
    assert "osmnx" in str(ei.value)
    assert not isinstance(ei.value, AttributeError)


def test_import_lazy_missing_submodule_is_attribute_error(monkeypatch):
    _patch_import(
        monkeypatch,
        ModuleNotFoundError("No module named 'pysewer.sources'", name="pysewer.sources"),
    )
    with pytest.raises(AttributeError, match="sources"):
        pysewer._import_lazy("sources")


def test_import_lazy_internal_module_error_is_reraised(monkeypatch):
    _patch_import(
        monkeypatch,
        ModuleNotFoundError("No module named 'pysewer.helper'", name="pysewer.helper"),
    )
    with pytest.raises(ModuleNotFoundError) as ei:
        pysewer._import_lazy("sources")
    assert ei.value.name == "pysewer.helper"


def test_import_lazy_missing_dep_without_extra(monkeypatch):
    _patch_import(monkeypatch, ModuleNotFoundError("No module named 'foo'", name="foo"))
    with pytest.raises(ImportError, match="requires 'foo'") as ei:
        pysewer._import_lazy("metrics")
    assert "pip install" not in str(ei.value)


def test_dir_lists_plotting():
    assert "plotting" in dir(pysewer)


def test_unknown_name_does_not_import_plotting():
    r = _run(
        """
        import sys
        import pysewer
        assert hasattr(pysewer, "pytestmark") is False
        assert "matplotlib" not in sys.modules
        assert "pysewer.plotting" not in sys.modules
        """
    )
    assert r.returncode == 0, r.stderr


def test_unknown_name_with_matplotlib_blocked():
    r = _run(
        """
        import sys
        sys.modules["matplotlib"] = None
        sys.modules["mpl_toolkits"] = None
        import pysewer
        assert hasattr(pysewer, "pytestmark") is False
        try:
            pysewer.plot_model_domain
        except ImportError as e:
            assert "pysewer[plot]" in str(e), str(e)
        else:
            raise SystemExit("no ImportError")
        """
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_plotting_names_in_sync_with_plotting_module():
    import inspect

    import pysewer.plotting as plotting

    public = {
        n
        for n, f in inspect.getmembers(plotting, inspect.isfunction)
        if f.__module__ == "pysewer.plotting" and not n.startswith("_")
    }
    assert set(pysewer._PLOTTING_NAMES) == public
