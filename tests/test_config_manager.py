# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

import json
from copy import deepcopy

import pytest

import pysewer
from pysewer.config.manager import get_config, reset_config, set_config
from pysewer.config.settings import DEFAULT_SETTINGS_PATH, load_config, load_settings


@pytest.fixture(autouse=True)
def restore_config():
    """
    Keep tests isolated by resetting the global config before and after.
    """
    reset_config()
    yield
    reset_config()


def _modified_settings(tmp_path, *, dx=5, pump_penalty=42, min_slope=-0.123):
    """
    Create a full custom settings file derived from the defaults with a few overrides.
    """
    settings = load_settings(DEFAULT_SETTINGS_PATH)
    settings["preprocessing"]["dx"] = dx
    settings["preprocessing"]["pump_penalty"] = pump_penalty
    settings["optimization"]["min_slope"] = min_slope
    dest = tmp_path / "custom_settings.yaml"
    dest.write_text(json.dumps(settings))
    return dest


def test_default_config_matches_settings_yaml():
    """The default Config should mirror the values from settings.yaml."""
    raw_settings = load_settings(DEFAULT_SETTINGS_PATH)
    config = get_config()

    assert config.preprocessing.dx == raw_settings["preprocessing"]["dx"]
    assert config.preprocessing.pump_penalty == raw_settings["preprocessing"]["pump_penalty"]
    assert config.optimization.min_slope == raw_settings["optimization"]["min_slope"]


def test_load_config_with_custom_path_overrides_defaults(tmp_path):
    """Loading with a custom settings file should override the defaults."""
    custom_path = _modified_settings(tmp_path, dx=1, pump_penalty=999, min_slope=-0.5)

    config = load_config(custom_path=str(custom_path))

    assert config.preprocessing.dx == 1
    assert config.preprocessing.pump_penalty == 999
    assert config.optimization.min_slope == -0.5


def test_set_config_updates_global_instance(tmp_path):
    """set_config should update the shared config used by get_config."""
    custom_path = _modified_settings(tmp_path, dx=3)

    set_config(custom_path=str(custom_path))

    updated = get_config()
    assert updated.preprocessing.dx == 3


def test_custom_settings_dict_overrides(tmp_path):
    """Using a custom settings dict should override defaults for all modules."""
    custom_settings = load_settings(DEFAULT_SETTINGS_PATH)
    custom_settings["preprocessing"]["dx"] = 7
    custom_settings["optimization"]["tmax"] = 12.34

    updated = set_config(custom_settings_dict=deepcopy(custom_settings))

    assert updated.preprocessing.dx == 7
    assert updated.optimization.tmax == 12.34


def test_set_custom_config_updates_package_default(tmp_path):
    """
    The public helper should keep pysewer.DEFAULT_CONFIG in sync with the manager.
    """
    custom_settings = load_settings(DEFAULT_SETTINGS_PATH)
    custom_settings["preprocessing"]["dx"] = 11

    pysewer.set_custom_config(custom_settings_dict=deepcopy(custom_settings))

    assert pysewer.DEFAULT_CONFIG.preprocessing.dx == 11
    assert get_config().preprocessing.dx == 11
