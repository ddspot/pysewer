# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import geopandas as gpd
import networkx as nx
import pandas as pd
import yaml

# get the directory of the current file
current_directory = Path(os.path.dirname(os.path.realpath(__file__)))

DEFAULT_SETTINGS_PATH = str(current_directory / "settings.yaml")


@dataclass
class Preprocessing:
    """Preprocessing settings."""

    dem_file_path: str | None = None
    roads_input_data: str | gpd.GeoDataFrame | None = None
    buildings_input_data: str | gpd.GeoDataFrame | None = None
    dx: int = 10
    pump_penalty: int = 1000
    max_connection_length: int = 30
    clustering: str = "none"
    connect_buildings: bool = True
    field_get_sinks: str = "type"
    field_get_buildings: str = "type"
    value_get_sinks: str = "sink"
    value_get_buildings: str = "building"
    add_private_sewer: bool = True
    combined_sewer_factor: float = 1.0


@dataclass
class Optimization:
    inhabitants_dwelling_attribute_name: str
    inhabitants_dwelling: int
    daily_wastewater_person: float
    peak_factor: float
    min_slope: float
    max_slope: float
    tmax: float
    tmin: float
    inflow_trench_depth: float
    min_trench_depth: float = 0.0
    diameters: list[float] = field(default_factory=list)
    roughness: float = 0.013
    pressurized_diameter: float = 0.2
    min_cover: float = 0.25
    min_pipe_length: float = 2.0
    velocity_min: float = 0.7
    velocity_max: float = 3.0
    max_depth_ratio: float = 0.75


@dataclass
class Plotting:
    plot_connection_graph: bool
    plot_junction_graph: bool
    plot_sink: bool
    plot_sewer: bool
    hillshade: bool
    colormap: str
    sewer_graph: nx.Graph | None = None
    info_table: dict | None = None


@dataclass
class Export:
    file_format: str


@dataclass
class Config:
    preprocessing: Preprocessing
    optimization: Optimization
    plotting: Plotting
    export: Export


def load_settings(file_path: str) -> dict:
    """Load settings from a YAML file."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Settings file not found: {file_path}")

    with open(file_path) as file:
        settings = yaml.safe_load(file)
    return settings


def deep_merge(source: dict, destination: dict) -> dict:
    """
    Deep merge two dictionaries. Source values override destination values.

    Parameters
    ----------
    source : dict
        Dictionary with values to override
    destination : dict
        Dictionary with default values

    Returns
    -------
    dict
        Merged dictionary
    """
    for key, value in source.items():
        if isinstance(value, dict):
            node = destination.setdefault(key, {})
            if isinstance(node, dict):
                deep_merge(value, node)
            else:
                destination[key] = value
        else:
            destination[key] = value
    return destination


def validate_settings(settings: dict) -> None:
    """Validate the settings dictionary structure."""
    required_sections = ["preprocessing", "optimization", "plotting", "export"]
    for section in required_sections:
        if section not in settings:
            raise ValueError(f"Missing required section in settings: {section}")


def override_settings(
    custom_path: str | None = None, custom_setting_dict: dict | None = None
) -> dict:
    """
    Override default settings with custom settings.

    Parameters
    ----------
    custom_path : str, optional
        Path to custom settings YAML file
    custom_setting_dict : dict, optional
        Dictionary with custom settings

    Returns
    -------
    dict
        Merged settings dictionary
    """
    # Load default settings
    settings = load_settings(DEFAULT_SETTINGS_PATH)

    if custom_path and custom_setting_dict:
        raise ValueError("Provide either custom_path or custom_setting_dict, not both")

    if custom_path:
        custom_settings = load_settings(custom_path)
        settings = deep_merge(custom_settings, settings)
    elif custom_setting_dict:
        settings = deep_merge(custom_setting_dict, settings)

    validate_settings(settings)
    return settings


def dict_to_config(settings_dict: dict) -> Config:
    """Convert settings dictionary to Config object."""
    try:
        preprocessing_config = Preprocessing(**settings_dict["preprocessing"])
        optimization_config = Optimization(**settings_dict["optimization"])
        plotting_config = Plotting(**settings_dict["plotting"])
        export_config = Export(**settings_dict["export"])

        return Config(
            preprocessing=preprocessing_config,
            optimization=optimization_config,
            plotting=plotting_config,
            export=export_config,
        )
    except TypeError as e:
        raise ValueError(f"Invalid settings structure: {e!s}")


def load_config(
    custom_path: str | None = None, custom_setting_dict: dict | None = None
) -> Config:
    """
    Load configuration with optional overrides.
    """
    logger = logging.getLogger(__name__)

    if custom_path and custom_setting_dict:
        raise ValueError("Provide either custom_path or custom_setting_dict, not both")

    # Load default settings
    base_settings_dict = load_settings(DEFAULT_SETTINGS_PATH)
    merged_settings = base_settings_dict

    # If custom path is provided, load and deep-merge with defaults
    if custom_path:
        logger.info(f"Loading custom settings from: {custom_path}")
        custom_settings_dict = load_settings(custom_path)
        merged_settings = deep_merge(custom_settings_dict, base_settings_dict)
    # If custom settings dict is provided, deep-merge with defaults
    elif custom_setting_dict:
        logger.info("Applying custom settings dictionary")
        merged_settings = deep_merge(custom_setting_dict, base_settings_dict)

    config = dict_to_config(merged_settings)

    logger.info("Settings loaded:")
    logger.info(f"  tmax: {config.optimization.tmax}")
    logger.info(f"  tmin: {config.optimization.tmin}")
    logger.info(f"  min_slope: {config.optimization.min_slope}")
    logger.info(f"  pump_penalty: {config.preprocessing.pump_penalty}")

    return config


def config_to_dict(config: Config) -> dict:
    """Convert Config object to dictionary."""
    return asdict(config)


def flatten_config(config_dict, parent_key="", sep="_"):
    items = {}
    for k, v in config_dict.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.update(flatten_config(v, new_key, sep=sep))
        else:
            items[new_key] = v
    return items


def config_to_dataframe(config: Config) -> pd.DataFrame:
    config_dict = config_to_dict(config)
    flat_config = flatten_config(config_dict)
    df = pd.DataFrame(list(flat_config.items()), columns=["Setting", "Value"])
    return df


# view defaults settings
def view_default_settings():
    default_settings = load_config(DEFAULT_SETTINGS_PATH)
    df = config_to_dataframe(default_settings)
    return df


# if __name__ == "__main__":
#     # test the deault settings using config class

#     Config1 = load_config()
#     print(Config1)
#     print(type(Config1))

#     # assessing values
#     print(Config1.preprocessing.dx)

#     print("Done!!")
