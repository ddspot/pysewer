# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""
Lightweight configuration manager used across the package.

A small wrapper that keeps a module-level Config instance so that modules
can share overrides (e.g. set via `set_custom_config`). The public helpers
mirror the functions used elsewhere in the codebase.
"""

from .settings import load_config, Config

# Store a single config instance that can be shared package-wide
_CONFIG: Config = load_config()


def get_config() -> Config:
    """Return the current global config instance."""
    return _CONFIG


def set_config(custom_path=None, custom_settings_dict=None) -> Config:
    """
    Override the global config with a custom path or settings dict and return it.
    """
    global _CONFIG
    _CONFIG = load_config(custom_path, custom_settings_dict)
    return _CONFIG


def reset_config() -> Config:
    """
    Reset the global config back to the defaults from settings.yaml.
    """
    return set_config()
