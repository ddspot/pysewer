# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

import itertools
import warnings
from operator import itemgetter
from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import shapely
from scipy.spatial import cKDTree, distance
from shapely.geometry import *
from shapely.geometry import LineString, Point


def get_upstream_nodes(G: nx.DiGraph, start_node, field: str, value: str) -> list:
    """
    Returns a list of all upstream nodes in a directed graph `G` that have a node attribute `field` with value `value`,
    starting from `start_node` and traversing the graph in reverse order using a breadth-first search algorithm.

    Parameters
    ----------
    G : networkx.DiGraph
        The directed graph to traverse.
    start_node : hashable
        The node to start the traversal from.
    field : str
        The name of the node attribute to filter by.
    value : Any
        The value of the node attribute to filter by.

    Returns
    -------
    List
        A list of all upstream nodes in `G` that have a node attribute `field` with value `value`.
    """
    return [
        n
        for n in nx.traversal.bfs_tree(G, start_node, reverse=True)
        if G.nodes(data=field, default=None)[n] == value
    ]


def get_path_distance(detailed_path: list[tuple]) -> float:
    """
    Calculates the total distance of a path given a list of detailed path coordinates.

    Parameters
    ----------
    detailed_path : List[tuple]
        A list of tuples representing the detailed path coordinates.

    Returns
    -------
    float
        The total distance of the path.

    Examples
    --------
    >>> get_path_distance([(0, 0), (3, 4), (7, 1)])
    9.848857801796104
    """
    edgesinpath = itertools.pairwise(detailed_path)
    path_dist = 0
    for e in edgesinpath:
        path_dist += distance.euclidean(e[0], e[1])
    return path_dist


def get_closest_edge(G: nx.Graph, point: shapely.geometry.Point) -> tuple:
    """
    Returns the closest edge to a given point in a networkx graph.

    Parameters:
    -----------
    G : networkx.Graph
        The graph to search for the closest edge.
    point : shapely.geometry.Point
        The point to search for the closest edge.

    Returns:
    --------
    closest_edge : tuple
        A tuple representing the closest edge to the given point.
    """
    edge_gdf = get_edge_gdf(G)
    edge_gdf["closest_edge"] = edge_gdf.geometry.tolist()
    cc = ckdnearest(
        gpd.GeoDataFrame(geometry=[point]), edge_gdf, ["closest_edge"]
    ).iloc[0, 1]
    # Validate the returned type
    if not isinstance(cc, tuple):
        raise ValueError(
            "Expected a tuple representing an edge but got a different type."
        )

    return cc


def get_closest_edge_multiple(G: nx.Graph, list_of_points: list) -> list:
    """
    Returns a list of the closest edges in a networkx graph to a list of points.

    Parameters
    ----------
    G : networkx.Graph
        A networkx graph object.
    list_of_points : list
        A list of shapely Point objects.

    Returns
    -------
    list
        A list of shapely LineString objects representing the closest edges to the input points.
    """
    edge_gdf = get_edge_gdf(G)
    edge_gdf["closest_edge"] = edge_gdf.geometry.tolist()

    # check if list of points or edge is empty or contains invalids values
    if (
        not list_of_points
        or edge_gdf.empty
        or any(np.isnan(x.x) or np.isnan(x.y) for x in list_of_points)
    ):
        warnings.warn("Skippind due to empty or invalid list of points or edges.", stacklevel=2)
        return []

    cc = ckdnearest(
        gpd.GeoDataFrame(geometry=list_of_points), edge_gdf, ["closest_edge"]
    )["closest_edge"].to_list()
    return cc


def get_edge_gdf(
    G: nx.Graph,
    field: str | None = None,
    value: Any | None = None,
    detailed: bool = False,
) -> gpd.GeoDataFrame:
    """
    Returns a GeoDataFrame of edges in a networkx graph that match a given field and value.

    Parameters
    ----------
    G : networkx.Graph
        The graph to extract edges from.
    field : str, optional
        The edge attribute to filter on.
    value : any, optional
        The value to filter the edge attribute on.
    detailed : bool, optional
        If True, returns a detailed GeoDataFrame with all edge attributes. If False, returns a simplified GeoDataFrame with only the edge geometry.

    Returns
    -------
    gpd.GeoDataFrame
        A GeoDataFrame of edges that match the given field and value.
    """
    edges = get_edge_keys(G, field=field, value=value)
    # If no matching edges are found, return an empty GeoDataFrame
    if not edges:
        return gpd.GeoDataFrame()

    data = [G[edge[0]][edge[1]] for edge in edges]

    if detailed:
        return gpd.GeoDataFrame(data)
    else:
        return gpd.GeoDataFrame(geometry=[LineString(e) for e in edges])


def get_node_gdf(G: nx.Graph, field=None, value=None) -> gpd.GeoDataFrame:
    """
    Returns a GeoDataFrame of nodes in the graph that match the specified field and value.

    Parameters
    ----------
    G : nx.Graph
        The input graph.
    field : str, optional
        The node attribute to filter on.
    value : str, optional
        The value to filter on.

    Returns
    -------
    gdf : gpd.GeoDataFrame
        A GeoDataFrame of nodes that match the specified field and value.

    Raises
    ------
    ValueError
        If no geometry column is found in the GeoDataFrame.

    Notes
    -----
    This function filters the nodes in the input graph based on the specified field and value, and returns a GeoDataFrame
    containing the filtered nodes and their attributes. The GeoDataFrame is created using the coordinates of the filtered
    nodes and their attributes.

    """
    coord_tuples = get_node_keys(G, field=field, value=value)
    try:
        x, y = zip(*coord_tuples, strict=False)
        data = [G.nodes(data=True)[n] for n in coord_tuples]
        gdf = gpd.GeoDataFrame(data, geometry=gpd.points_from_xy(x, y))
        if "geometry" not in gdf.columns:
            raise ValueError("No geometry column found in the GeoDataFrame.")

        return gdf.set_geometry("geometry")

    except:
        return gpd.GeoDataFrame()


def get_node_keys(G: nx.Graph, field: str | None = None, value: str | None = None):
    """
    Returns a list of keys for nodes in the graph `G` that have a specified `field` with a specified `value`.

    Parameters:
    -----------
    G : networkx.Graph
        The graph to search for nodes.
    field : str, optional
        The field to search for in the node data dictionary. If None, all nodes are returned.
    value : str
        The value to search for in the specified `field`. If None, all nodes with the specified `field` are returned.

    Returns:
    --------
    list
        A list of keys for nodes in the graph `G` that have a specified `field` with a specified `value`.
    """
    return [x for x, y in G.nodes(data=field, default=None) if y == value]


def get_edge_keys(G, field=None, value=None):
    """
    Returns a list of edge keys (tuples of nodes) for the given graph `G` that have an edge attribute `field` with value `value`.

    Parameters:
    -----------
    G : networkx.Graph
        The graph to search for edges.
    field : str, optional
        The name of the edge attribute to filter on.
    value : any, optional
        The value of the edge attribute to filter on.

    Returns:
    --------
    list of tuples
        A list of edge keys (tuples of nodes) that have an edge attribute `field` with value `value`.
    """
    return [(u, v) for u, v, e in G.edges(data=field, default=None) if e == value]


def get_path_gdf(G, upstream, downstream):
    """
    Returns a GeoDataFrame containing the geometry of the shortest path between two nodes in a graph.

    Parameters:
    -----------
    G : networkx.Graph
        The graph to find the shortest path in.
    upstream : int
        The starting node of the path.
    downstream : int
        The ending node of the path.

    Returns:
    --------
    gpd.GeoDataFrame
        A GeoDataFrame containing the geometry of the shortest path between the upstream and downstream nodes.
    """
    path = nx.dijkstra_path(G, upstream, downstream)
    edges = list(itertools.pairwise(path))
    return gpd.GeoDataFrame(geometry=[G[e[0]][e[1]]["geometry"] for e in edges])


def get_mean_slope(
    G: nx.MultiDiGraph, upstream: int, downstream: int, us_td: float, ds_td: float
) -> float:
    """
    Calculates the mean slope of a path between two nodes in a graph.

    Parameters:
    -----------
    G : networkx.MultiDiGraph
        A directed graph object.
    upstream : int
        The upstream node ID.
    downstream : int
        The downstream node ID.
    us_td : float
        The upstream node topographic elevation.
    ds_td : float
        The downstream node topographic elevation.

    Returns:
    --------
    float
        The mean slope of the path between the upstream and downstream nodes.
    """
    gdf = get_path_gdf(G, upstream, downstream)
    length = gdf["geometry"].length.sum()
    return (ds_td - us_td) / length


def ckdnearest(
    gdfA: gpd.GeoDataFrame, gdfB: gpd.GeoDataFrame, gdfB_cols=None
) -> gpd.GeoDataFrame:
    """
    Returns a GeoDataFrame containing the closest geometry and attributes from gdfB to each geometry in gdfA.
    """
    # resetting the index of gdfA and gdfB here.
    if gdfB_cols is None:
        gdfB_cols = ["closest_edge"]
    gdfA = gdfA.reset_index(drop=True)
    gdfB = gdfB.reset_index(drop=True)
    A = np.concatenate([np.array(geom.coords) for geom in gdfA.geometry.to_list()])
    B = [np.array(geom.coords) for geom in gdfB.geometry.to_list()]
    B_ix = tuple(
        itertools.chain.from_iterable(
            [itertools.repeat(i, x) for i, x in enumerate(list(map(len, B)))]
        )
    )
    B = np.concatenate(B)
    ckd_tree = cKDTree(B)

    # check if A or B is empty or contains invalids values
    if len(A) == 0 or len(B) == 0 or np.isnan(A).any() or np.isnan(B).any():
        warnings.warn("Skippind due to empty or invalid list of points or edges.", stacklevel=2)
        return gdfA

    dist, idx = ckd_tree.query(A, k=1)
    idx = itemgetter(*idx)(B_ix)
    gdf = pd.concat(
        [
            gdfA,
            gdfB.loc[idx, gdfB_cols].reset_index(drop=True),
            pd.Series(dist, name="dist"),
        ],
        axis=1,
    )
    return gdf


def remove_third_dimension(geom):
    """
    remove the third dimension of a shapely geometry
    """
    if geom.is_empty:
        return geom

    if isinstance(geom, Polygon):
        exterior = geom.exterior
        new_exterior = remove_third_dimension(exterior)

        interiors = geom.interiors
        new_interiors = []
        for int in interiors:
            new_interiors.append(remove_third_dimension(int))

        return Polygon(new_exterior, new_interiors)

    elif isinstance(geom, LinearRing):
        return LinearRing([xy[0:2] for xy in list(geom.coords)])

    elif isinstance(geom, LineString):
        return LineString([xy[0:2] for xy in list(geom.coords)])

    elif isinstance(geom, Point):
        return Point([xy[0:2] for xy in list(geom.coords)])

    elif isinstance(geom, MultiPoint):
        points = list(geom.geoms)
        new_points = []
        for point in points:
            new_points.append(remove_third_dimension(point))

        return MultiPoint(new_points)

    elif isinstance(geom, MultiLineString):
        lines = list(geom.geoms)
        new_lines = []
        for line in lines:
            new_lines.append(remove_third_dimension(line))

        return MultiLineString(new_lines)

    elif isinstance(geom, MultiPolygon):
        pols = list(geom.geoms)

        new_pols = []
        for pol in pols:
            new_pols.append(remove_third_dimension(pol))

        return MultiPolygon(new_pols)

    elif isinstance(geom, GeometryCollection):
        geoms = list(geom.geoms)

        new_geoms = []
        for geom in geoms:
            new_geoms.append(remove_third_dimension(geom))

        return GeometryCollection(new_geoms)

    else:
        raise RuntimeError(
            f"Currently this type of geometry is not supported: {type(geom)}"
        )


def is_valid_geometry(geometry):
    """
    Check if a geometry is valid.
    """
    return geometry.is_valid and not geometry.is_empty


def get_sewer_info(G):
    """
    Returns a dictionary with information about the sewer network.

    Parameters
    ----------
    G : networkx.Graph
        The sewer network graph.

    Returns
    -------
    dict
        A dictionary containing the following information:
        - Total Buildings: Total number of buildings in the network.
        - Pressurized Sewer Length [m]: Total length of pressurized sewers in
          meters.
        - Gravity Sewer Length [m]: Total length of gravity sewers in meters.
        - Lifting Stations: Total number of lifting stations in the network.
        - Pumping Stations: Total number of pumping stations in the network
          (excluding those located in buildings).
        - Private Pumps: Total number of pumps located in buildings.

    """
    info = {}
    buildings = get_node_keys(G, field="node_type", value="building")
    info["Total Buildings"] = len(buildings)

    # extract the presure and gravity sewer geodataframes
    p_sewer_gdf = get_edge_gdf(G, field="pressurized", value=True)
    g_sewer_gdf = get_edge_gdf(G, field="pressurized", value=False)
    info["Pressurized Sewer Length [m]"] = (
        round(p_sewer_gdf.geometry.length.sum()) if not p_sewer_gdf.empty else 0
    )
    info["Gravity Sewer Length [m]"] = (
        round(g_sewer_gdf.geometry.length.sum()) if not g_sewer_gdf.empty else 0
    )

    # extract the number of lifting and pumping stations
    pumps = get_node_keys(G, field="pumping_station", value=True)
    lifting_stations = get_node_keys(G, field="lifting_station", value=True)

    info["Pumping Stations"] = (
        len([n for n in pumps if n not in buildings]) if pumps else 0
    )
    info["Lifting Stations"] = len(lifting_stations) if lifting_stations else 0
    info["Private Pumps"] = (
        len([n for n in buildings if n in pumps])
        if [n for n in buildings if n in pumps]
        else 0
    )

    return info
