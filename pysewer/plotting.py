# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only


import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import rasterio.plot
from mpl_toolkits.axes_grid1 import make_axes_locatable
from rasterio.plot import plotting_extent

from .config.manager import get_config
from .helper import get_edge_gdf, get_node_gdf


def _hillshade(arr: np.ndarray, azimuth: float = 30, altitude: float = 30):
    """
    Hillshade of a DEM array (ESRI formula, same as the former earthpy
    dependency).
    """
    azimuth = 360.0 - azimuth
    x, y = np.gradient(arr)
    slope = np.pi / 2.0 - np.arctan(np.sqrt(x * x + y * y))
    aspect = np.arctan2(-x, y)
    azm_rad = azimuth * np.pi / 180.0
    alt_rad = altitude * np.pi / 180.0
    shaded = np.sin(alt_rad) * np.sin(slope) + np.cos(alt_rad) * np.cos(
        slope
    ) * np.cos((azm_rad - np.pi / 2.0) - aspect)
    return 255 * (shaded + 1) / 2

# def get_plot_pos(G):
#    pos = dict(G.nodes)
#    for k in pos.keys():
#        pos[k] = np.array(k)
#    return(pos)


def plot_model_domain(
    modelDomain,
    plot_connection_graph: bool | None = None,
    plot_junction_graph: bool | None = None,
    plot_sink: bool | None = None,
    plot_sewer: bool | None = None,
    sewer_graph: nx.Graph | None = None,
    info_table: dict | None = None,
    hs_alt=30,
    hs_az=0,
    hillshade: bool | None = None,
    fig_size: tuple = (20, 20),
):
    """
    Plots the sewer network model domain.

    Parameters
    ----------
    modelDomain : pysewer.ModelDomain
        The model domain to plot.
    plot_connection_graph : bool, optional
        Whether to plot the connection graph, by default False.
    plot_junction_graph : bool, optional
        Whether to plot the junction graph, by default False.
    plot_sink : bool, optional
        Whether to plot the sink, by default True.
    plot_sewer : bool, optional
        Whether to plot the sewer, by default False.
    sewer_graph : networkx.Graph, optional
        The sewer graph to plot, by default None.
    info_table : dict, optional
        The information table to plot, by default None.
    hs_alt : int, optional
        The altitude of the hillshade, by default 30.
    hs_az : int, optional
        The azimuth of the hillshade, by default 0.
    hillshade : bool, optional
        Whether to plot the hillshade, by default False.
    plot_problematic_points : bool, optional
        Whether to plot the problematic points, by default False.

    Returns
    -------
    fig, ax : matplotlib.figure.Figure, matplotlib.axes.Axes
        The figure and axes of the plot.
    """
    config = get_config()
    plot_connection_graph = (
        config.plotting.plot_connection_graph
        if plot_connection_graph is None
        else plot_connection_graph
    )
    plot_junction_graph = (
        config.plotting.plot_junction_graph
        if plot_junction_graph is None
        else plot_junction_graph
    )
    plot_sink = config.plotting.plot_sink if plot_sink is None else plot_sink
    plot_sewer = config.plotting.plot_sewer if plot_sewer is None else plot_sewer
    hillshade = config.plotting.hillshade if hillshade is None else hillshade
    sewer_graph = config.plotting.sewer_graph if sewer_graph is None else sewer_graph
    info_table = config.plotting.info_table if info_table is None else info_table

    fig, ax = plt.subplots(figsize=fig_size)
    bbox = get_edge_gdf(modelDomain.connection_graph).total_bounds

    ax.set_xlim(bbox[0] - 100, bbox[2] + 100)
    ax.set_ylim(bbox[1] - 100, bbox[3] + 100)
    get_node_gdf(
        modelDomain.connection_graph, field="node_type", value="building"
    ).plot(ax=ax, marker="s", color="black", markersize=5, label="Buildings", zorder=4)

    modelDomain.roads.gdf.plot(
        ax=ax, label="Roads", linewidth=1, color="k", zorder=1, linestyle="dashed"
    )

    if hillshade:
        rasterio.plot.show(
            modelDomain.dem.raster,
            contour=True,
            colors="grey",
            ax=ax,
            levels=30,
            alpha=0.5,
        )
        rasterio.plot.show(modelDomain.dem.raster, ax=ax, cmap="Greys_r")
        # Create and plot the hillshade
        elevation = modelDomain.dem.raster.read(1)
        # Set masked values to np.nan
        elevation = elevation.astype(float)
        elevation[elevation < 0] = np.nan
        hillshade = _hillshade(elevation, altitude=hs_alt, azimuth=hs_az)

        ax.imshow(
            hillshade,
            extent=plotting_extent(modelDomain.dem.raster),
            cmap="Greys_r",
            alpha=0.8,
        )
        ax.set_title("Hillshade made from DTM")

    if plot_connection_graph:
        get_edge_gdf(modelDomain.connection_graph).plot(
            ax=ax, color="g", zorder=5, label="Connection Graph"
        )
    if plot_junction_graph:
        get_edge_gdf(modelDomain.junction_graph).plot(
            ax=ax, color="g", zorder=5, label="Junction Graph"
        )
    if plot_sink:
        get_node_gdf(
            modelDomain.connection_graph, field="node_type", value="wwtp"
        ).plot(ax=ax, marker="o", color="g", markersize=50, label="WWTP")
    if plot_sewer:
        # check if the field for the sewer graph prvided is not empty
        if get_node_gdf(sewer_graph, field="pumping_station", value=True).empty:
            print("No pumping station in the sewer graph")
            print("Plotting sewer graph without pumping station")
            get_edge_gdf(sewer_graph, detailed=True).plot(
                ax=ax, color="b", markersize=50, zorder=5, label="Sewer Layout"
            )
        else:
            get_node_gdf(sewer_graph, field="pumping_station", value=True).plot(
                ax=ax,
                marker="^",
                color="red",
                markersize=50,
                zorder=6,
                label="Pumping Station",
            )

            get_node_gdf(sewer_graph, field="lifting_station", value=True).plot(
                ax=ax,
                marker="^",
                color="mediumseagreen",
                markersize=50,
                zorder=6,
                label="Lifting Station",
            )

            get_edge_gdf(
                sewer_graph, field="pressurized", value=False, detailed=True
            ).plot(ax=ax, color="b", markersize=50, zorder=5, label="Gravity Sewers")

            get_edge_gdf(
                sewer_graph, field="pressurized", value=True, detailed=True
            ).plot(
                ax=ax, color="r", markersize=50, zorder=5, label="Pressurized Sewers"
            )

    if info_table is not None:
        data = [[info_table[key]] for key in info_table]
        ax.table(
            cellText=data,
            rowLabels=list(info_table.keys()),
            colLabels=["Sewer Network Metrics"],
            loc="lower left",
            zorder=10,
            bbox=[0.33, 0.01, 0.3, 0.15],
        )

    ax.set_title("Sewer Network Plot")
    ax.set_xlabel("Easting")
    ax.set_ylabel("Northing")
    plt.legend(loc="upper right")

    return fig, ax


def plot_sewer_attributes(
    modelDomain,
    sewer_graph,
    attribute,
    colormap="jet",
    title="Sewer Network Plot",
    hillshade=False,
    plot_sink=True,
    fig_size=(20, 20),
):
    """
    Plots the sewer network with the specified attribute.

    Parameters
    ----------
    modelDomain : object
        The model domain object.
    sewer_graph : object
        The sewer graph object.
    attribute : str
        The attribute to plot.
    colormap : str, optional
        The colormap to use for the plot. Default is "jet".
    title : str, optional
        The title of the plot. Default is "Sewer Network Plot".
    hillshade : bool, optional
        Whether to include a hillshade in the plot. Default is False.
    plot_sink : bool, optional
        Whether to plot the sinks (WWTP) in the sewer network. Default is True.
    fig_size : tuple, optional
        The size of the figure in inches. Default is (20, 20).

    Returns
    -------
    fig : matplotlib.figure.Figure
        The figure object.
    ax : matplotlib.axes.Axes
        The axes object.
    """
    hs_alt = 30
    hs_az = 0
    fig, ax = plt.subplots(figsize=fig_size)

    divider = make_axes_locatable(ax)
    cax = divider.append_axes("right", size="5%", pad=0.2)  # depends on the user needs
    bbox = get_edge_gdf(modelDomain.connection_graph).total_bounds

    ax.set_xlim(bbox[0] - 100, bbox[2] + 100)
    ax.set_ylim(bbox[1] - 100, bbox[3] + 100)
    get_node_gdf(
        modelDomain.connection_graph, field="node_type", value="building"
    ).plot(ax=ax, marker="s", color="black", markersize=5, label="Buildings", zorder=4)

    modelDomain.roads.gdf.plot(
        ax=ax, label="Roads", linewidth=1, color="k", zorder=1, linestyle="dashed"
    )

    if hillshade:
        rasterio.plot.show(
            modelDomain.dem.raster,
            contour=True,
            colors="grey",
            ax=ax,
            levels=30,
            alpha=0.5,
        )
        rasterio.plot.show(modelDomain.dem.raster, ax=ax, cmap="Greys_r")
        # Create and plot the hillshade
        elevation = modelDomain.dem.raster.read(1)
        # Set masked values to np.nan
        elevation = elevation.astype(float)
        elevation[elevation < 0] = np.nan
        hillshade = _hillshade(elevation, altitude=hs_alt, azimuth=hs_az)

        ax.imshow(
            hillshade,
            extent=plotting_extent(modelDomain.dem.raster),
            cmap="Greys_r",
            alpha=0.8,
        )

    if plot_sink:
        get_node_gdf(
            modelDomain.connection_graph, field="node_type", value="wwtp"
        ).plot(ax=ax, marker="o", color="g", markersize=50, label="WWTP")

    get_edge_gdf(sewer_graph, detailed=True).plot(
        ax=ax, column=attribute, cmap=colormap, cax=cax, legend=True
    )
    ax.set_title(title)
    return fig, ax


def plot_connection_graph(model_domain, fig_size=(10, 10)):
    """
    Plots the connection graph generated by the ModelDomain.generate_connection_graph() method.

    Parameters
    ----------
    model_domain : pysewer.ModelDomain
        The model domain containing the connection graph.
    fig_size : tuple, optional
        The size of the figure in inches. Default is (20, 20).

    Returns
    -------
    fig : matplotlib.figure.Figure
        The figure object.
    ax : matplotlib.axes.Axes
        The axes object.
    """
    # Generate the connection graph if it hasn't been generated yet
    if not hasattr(model_domain, "connection_graph"):
        model_domain.connection_graph = model_domain.generate_connection_graph()

    # Create a base plot using the existing plot_model_domain function
    fig, ax = plot_model_domain(
        model_domain,
        plot_connection_graph=False,
        plot_junction_graph=False,
        plot_sink=True,
        plot_sewer=False,
        hillshade=True,
        fig_size=fig_size,
    )

    # Plot the edges of the connection graph
    edge_gdf = get_edge_gdf(model_domain.connection_graph)
    edge_gdf.plot(ax=ax, color="blue", linewidth=1, zorder=3, label="Connections")

    # Plot the nodes of the connection graph
    node_gdf = get_node_gdf(model_domain.connection_graph)
    node_gdf.plot(ax=ax, color="red", markersize=20, zorder=4, label="Nodes")

    # Highlight nodes that need pumps
    pump_nodes = [
        node
        for node, data in model_domain.connection_graph.nodes(data=True)
        if data.get("needs_pump", False)
    ]
    if pump_nodes:
        pump_gdf = get_node_gdf(model_domain.connection_graph, value=pump_nodes)
        pump_gdf.plot(
            ax=ax, color="yellow", markersize=30, zorder=5, label="Pump Needed"
        )

    # Add labels for elevation to each node
    # for node, data in model_domain.connection_graph.nodes(data=True):
    #     elevation = data.get('elevation', 'N/A')
    #     if isinstance(elevation, (int, float)):
    #         elevation_text = f"{elevation:.1f}m"
    #     else:
    #         elevation_text = str(elevation)
    #     ax.annotate(elevation_text, (node[0], node[1]), xytext=(3, 3),
    #                 textcoords="offset points", fontsize=8, color='black')

    ax.set_title("Connection Graph")
    ax.legend()

    return fig, ax
