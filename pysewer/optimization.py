# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

import logging
import math
from typing import Hashable, List, Optional, Union

import networkx as nx
import numpy as np

from .config.manager import get_config
from .helper import get_mean_slope, get_node_keys, get_upstream_nodes
logger = logging.getLogger(__name__)

NodeType = Union[str, int, Hashable, None]


def place_lifting_station(G, node):
    """
    Places a lifting station at the specified node in the graph.

    Parameters
    ----------
    G : networkx.Graph
        The graph to add the lifting station to.
    node : int
        The node to add the lifting station to.

    Returns
    -------
    networkx.Graph
        The graph with the added lifting station.
    """
    node_attrs = {node: {"lifting_station": True}}
    nx.set_node_attributes(G, node_attrs)
    return G


def get_max_upstream_diameter(G: nx.DiGraph, edge: tuple):
    """
    Returns the maximum diameter of all upstream edges of the given edge in the directed graph G.

    Parameters
    ----------
    G : networkx.DiGraph
        The directed graph.
    edge : tuple
        The edge for which to find the maximum upstream diameter.

    Returns
    -------
    float
        The maximum diameter of all upstream edges of the given edge.
    """
    diameters = []
    for u, v, data in G.in_edges(edge[0], data=True):
        diameters.append(data["diameter"])
    return max(diameters)


def place_pump(G, node):
    """
    Places a pump at the specified node in the graph G and sets downstream edges "pressurized" attribute.

    Parameters
    ----------
    G : networkx.Graph
        The graph in which the pump is to be placed.
    node : hashable
        The node at which the pump is to be placed.

    Returns
    -------
    networkx.Graph
        The graph with the pump placed at the specified node and downstream edges "pressurized" attribute set.
    """
    if G.out_degree(node) != 1:
        print(node)
    node_attrs = {node: {"pumping_station": True}}
    nx.set_node_attributes(G, node_attrs)
    downstream = next(G.neighbors(node))
    edge_attrs = {(node, downstream): {"pressurized": True}}
    nx.set_edge_attributes(G, edge_attrs)
    return G


def set_diameter(G: nx.Graph, edge: tuple, diameter: float):
    """
    Set the diameter of an edge in a graph.

    Parameters:
    -----------
    G : networkx.Graph
        The graph to modify.
    edge : tuple
        The edge to modify.
    diameter : float
        The diameter to set.

    Returns:
    --------
    networkx.Graph
        The modified graph.
    """
    max_us = get_max_upstream_diameter(G, edge)
    if diameter < max_us:
        diameter = max_us
    edge_attrs = {(edge[0], edge[1]): {"diameter": diameter}}
    nx.set_edge_attributes(G, edge_attrs)
    return G


def get_downstream_junction(G: nx.Graph, node: int):
    """
    Returns the next downstream junction from the specified node in G.

    Parameters
    ----------
    G : networkx.Graph
        The graph to search for the downstream junction.
    node : int
        The node to start the search from.

    Returns
    -------
    int
        The downstream junction from the specified node in G.

    Notes
    -----
    The downstream junction is defined as the next junction in the graph that has a degree greater than 2 or an out-degree of 0.
    """
    junction = next(G.neighbors(node))
    while G.degree(junction) <= 2 and G.out_degree(junction) > 0:
        junction = next(G.neighbors(junction))
    return junction


def get_junction_front(G: nx.Graph, junctions):
    """
    Returns a list of junctions or terminals which have as many entries for inflow trench depths as they have incoming edges.

    Parameters
    ----------
    G : networkx.DiGraph
        A directed graph representing the sewer network.
    junctions : list
        A list of junctions or terminals in the sewer network.

    Returns
    -------
    list
        A list of junctions or terminals which have as many entries for inflow trench depths as they have incoming edges.
    """
    r = [
        n
        for n in G.nodes()
        if G.in_degree(n) == G.nodes[n]["upstream_traversed"] and n in junctions
    ]
    return r


def reverse_bfs(G, sink: str, include_private_sewer: bool = True):
    """
    Returns an iterator over edges in a sequential fashion, starting at the terminals (i.e. buildings) and returning all upstream edges of a junction before moving downstream

    Parameters
    ----------
    G : networkx.DiGraph
        The graph to traverse
    sink : str
        The node to start the traversal from
    include_private_sewer : bool, optional
        Whether to include private sewer nodes in the traversal, by default True

    Yields
    ------
    tuple
        A tuple representing an edge in the graph, in the form (source, target)
    """
    if not include_private_sewer:
        # reduce graph
        buildings = get_node_keys(G, field="node_type", value="building")
        # remove buildings
        G.remove_nodes_from(buildings)
    # We start from the sink in reversed graph direction
    bfs_edges = list(nx.edge_bfs(G, sink, orientation="reverse"))
    # We then flip the list and return the reversed edges to start from sources
    for edge in reversed(bfs_edges):
        yield ((edge[0], edge[1]))


def calculate_hydraulic_parameters(
    G,
    sinks: list,
    pressurized_diameter: Optional[float] = None,
    diameters: Optional[List[float]] = None,
    roughness: Optional[float] = None,
    include_private_sewer: Optional[bool] = None,
    combined_sewer_factor: float = 1.0,
):
    """
    Calculates hydraulic parameters for a sewer network graph.

    Parameters
    ----------
    G : networkx.Graph
        The sewer network graph.
    sinks : list
        A list of sink nodes in the graph.
    pressurized_diameter : float
        The diameter of pressurized pipes in the network.
    diameters : list
        A list of available pipe diameters.
    roughness : float
        The roughness coefficient of the pipes.
    include_private_sewer : bool, optional
        Whether to include private sewer connections in the graph, by default True.
    combined_sewer_factor : float, optional
        Multiplicative factor applied to the dry-weather peak wastewater flow to represent
        additional stormwater and inflow contributions in a combined sewer system. The
        design flow used for pipe sizing becomes:

            Q_design = Q_peak_wastewater * combined_sewer_factor

        where Q_peak_wastewater is computed in `estimate_peakflow` as

            Q_peak_wastewater = (((P * daily_wastewater_person)/24) * peak_factor) / 3600

        with P being the total upstream population. Typical values vary by catchment and
        design standard; use calibration (see Notes) if a reference network exists.

    Returns
    -------
    networkx.Graph
        The sewer network graph with updated hydraulic parameters.

    Notes
    -----
    This function places pumps/lifting stations on linear sections between road junctions.
    Three cases are possible:
    1. Terrain does not allow for gravity flow to the downstream node (this check uses the "needs_pump" attribute from the preprocessing
    to reduce computational load) -> place pump
    2. Terrain does not require pump but lowest inflow trench depth is too low for gravitational flow -> place lifting station
    3. Gravity flow is possible within given constraints

     Calibration tip
     --------------
     If you need the modeled design flows or aggregate volumes to match a known/reference
     combined system (e.g., legacy network capacity), you can iteratively adjust
     `combined_sewer_factor`:

     1. Choose an initial value (e.g., 1–10).
     2. Run `estimate_peakflow` (dry-weather) and then this function with that factor.
     3. Compute a comparison metric (e.g., sum of edge design flows at a key cross-section,
         maximum trunk flow, or total storage/throughput proxy) and compare to the reference.
     4. Update the factor using a simple proportional control, e.g.,
         factor_new = factor_old * (ReferenceValue / ModeledValue), and iterate until within tolerance.

     This approach scales the design flows uniformly and is a coarse substitute for a
     full hydrologic rainfall–runoff model when only reference totals are available.
    """
    config = get_config()
    pressurized_diameter = (
        config.optimization.pressurized_diameter
        if pressurized_diameter is None
        else pressurized_diameter
    )
    diameters = config.optimization.diameters if diameters is None else diameters
    roughness = config.optimization.roughness if roughness is None else roughness
    include_private_sewer = (
        config.preprocessing.add_private_sewer
        if include_private_sewer is None
        else include_private_sewer
    )
    min_trench_depth = config.optimization.min_trench_depth

    nx.set_node_attributes(G, [0], name="inflow_trench_depths")
    nx.set_node_attributes(G, [0], name="inflow_diameters")
    nx.set_edge_attributes(G, False, name="pressurized")
    edge_counter = 0
    buildings = get_node_keys(G, field="node_type", value="building")
    onsite_nodes = [n for n in G.nodes() if G.degree(n) == 0 and n in buildings]
    print(len(onsite_nodes))
    node_attrs = {n: {"onsite": True} for n in onsite_nodes}
    nx.set_node_attributes(G, node_attrs)
    for sink in sinks:
        if sink not in G:
            print(f"Sink {sink} not in graph")
        for edge in reverse_bfs(G, sink, include_private_sewer=include_private_sewer):
            # Node Values for trench depths cant be clearly defined for pumps and lifting stations because they have seperate incoming and outcoming values
            pressurized = False
            upstream = edge[0]
            downstream = edge[1]
            max_inflow_diameters = max(G.nodes[upstream]["inflow_diameters"])
            max_inflow_tds = max(G.nodes[upstream]["inflow_trench_depths"])
            profile = G.edges[edge]["profile"]

            # Does the edge need a pump starting at min td?
            needs_p, out_trench_depth, td_profile = needs_pump(
                profile=profile, inflow_trench_depth=min_trench_depth
            )
            if needs_p:
                G = place_pump(G, upstream)
                pressurized = True
                edge_attrs = {
                    edge: {
                        "trench_depth_profile": td_profile,
                        "mean_td": np.mean(
                            [topo[1] - td[1] for td, topo in zip(td_profile, profile)]
                        ),
                    }
                }

            else:
                # check if edge needs a lifting station based on max inflow td
                needs_p, out_trench_depth, td_profile = needs_pump(
                    profile=profile, inflow_trench_depth=max_inflow_tds
                )
                if needs_p:
                    G = place_lifting_station(G, upstream)
                    # update outflow td value now with lifting station at start of edge
                    _, out_trench_depth, td_profile = needs_pump(
                        profile=profile, inflow_trench_depth=min_trench_depth
                    )
                # append downstream inflow td value
                node_attrs = {
                    downstream: {
                        "inflow_trench_depths": G.nodes()[downstream][
                            "inflow_trench_depths"
                        ]
                        + [out_trench_depth]
                    }
                }
                nx.set_node_attributes(G, node_attrs)
                # add trenchdepth profile to edge:
                edge_attrs = {
                    edge: {
                        "trench_depth_profile": td_profile,
                        "mean_td": np.mean(
                            [topo[1] - td[1] for td, topo in zip(td_profile, profile)]
                        ),
                    }
                }
            # Diameter
            nx.set_edge_attributes(G, edge_attrs)

            slope = get_mean_slope(
                G, upstream, downstream, td_profile[0][1], td_profile[-1][1]
            )
            # Calculate diameter
            # adjust peak flow for combined sewer
            peak_flow = G.nodes[upstream]["peak_flow"] * combined_sewer_factor
            if pressurized:
                diam = pressurized_diameter
            else:
                diam = select_diameter(peak_flow, diameters, roughness, slope)
                if diam < max_inflow_diameters:
                    diam = max_inflow_diameters
            # Write results to graph
            edge_attrs = {
                edge: {
                    "diameter": diam,
                    "peak_flow": peak_flow,
                    "edge_counter": edge_counter,
                }
            }
            nx.set_edge_attributes(G, edge_attrs)
            edge_counter += 1
            node_attrs = {
                downstream: {
                    "inflow_trench_depths": G.nodes[downstream]["inflow_trench_depths"]
                    + [out_trench_depth]
                }
            }
            nx.set_node_attributes(G, node_attrs)

            # diameter
            node_attrs = {
                downstream: {
                    "inflow_diameters": G.nodes()[downstream]["inflow_diameters"]
                    + [diam]
                }
            }
            nx.set_node_attributes(G, node_attrs)

    return G


def estimate_peakflow(
    G: nx.Graph,
    inhabitants_dwelling: Optional[int] = None,
    daily_wastewater_person: Optional[float] = None,
    peak_factor: Optional[float] = None,
):
    """
    Estimate the peakflow in m³/s for a node n in Graph G.

    Parameters
    ----------
    G : networkx.Graph
        The graph to estimate peakflow for.
    inhabitants_dwelling : int
        The number of inhabitants per dwelling.
    daily_wastewater_person : float
        The daily wastewater generated per person in m³.
    peak_factor : float, optional
        The peak factor to use in the calculation, by default 2.3.

    Returns
    -------
    networkx.Graph
        The graph with updated node attributes for peak flow, average daily flow, and upstream pe.
    """
    logger.info("\n=== Dry Weather Flow  Estimation ===")

    config = get_config()
    inhabitants_dwelling = (
        config.optimization.inhabitants_dwelling
        if inhabitants_dwelling is None
        else inhabitants_dwelling
    )
    daily_wastewater_person = (
        config.optimization.daily_wastewater_person
        if daily_wastewater_person is None
        else daily_wastewater_person
    )
    peak_factor = (
        config.optimization.peak_factor if peak_factor is None else peak_factor
    )

    for n in G.nodes():
        upstream_buildings = get_upstream_nodes(G, n, "node_type", "building")
        logger.info(f"  Building node {n}: upstream_buildings={upstream_buildings}")
        total_population = 0
        # Sum up populations from each upstream building/block
        for building in upstream_buildings:
            building_data = G.nodes[building]
            # Try to get population in this priority:
            # 1. block_population (total population for the block)
            # 2. number_of_dwellings * inhabitants_dwelling
            # 3. fallback to single dwelling with inhabitants_dwelling
            if "block_population" in building_data:
                logger.info("  Using block_population")
                block_pop = float(building_data["block_population"])
            elif "number_of_dwellings" in building_data:
                logger.info("  Using number_of_dwellings")
                block_pop = (
                    float(building_data["number_of_dwellings"]) * inhabitants_dwelling
                )
            else:
                # Fallback: assume one dwelling with default inhabitants
                block_pop = inhabitants_dwelling

            total_population += block_pop

        # Calculate total daily wastewater volume
        upstream_daily = total_population * daily_wastewater_person
        # Calculate total upstream peak flow
        upstream_pe = total_population
        peak_flow = ((upstream_daily / 24) * peak_factor) / 3600
        logger.info(f"  Peak flow: {peak_flow:.6f} m³/s")
        logger.info(f"  Average daily flow: {upstream_daily:.6f} m³/s")
        logger.info(f"  Upstream peak flow: {upstream_pe:.6f} people")

        atr = {
            n: {
                "peak_flow": peak_flow,
                "average_daily_flow": upstream_daily,
                "upstream_pe": upstream_pe,
            }
        }
        nx.set_node_attributes(G, atr)
    return G


def mannings_equation(pipe_diameter: float, roughness: float, slope: float) -> float:
    """
    Calculates the volume flow rate of a pipe using Manning's equation.

    Parameters
    ----------
    pipe_diameter : float
        Diameter of the pipe in meters.
    roughness : float
        Roughness coefficient of the pipe.
    slope : float
        Slope of the pipe in units of elevation drop per unit length.

    Returns
    -------
    float
        Volume flow rate of the pipe in cubic meters per second.

    Raises
    ------
    ValueError
        If the slope is greater than 0.

    Notes
    -----
    Manning's equation is used to calculate the volume flow rate of a pipe based on its diameter, roughness coefficient, and slope.
    """
    # check slope for correctness:
    if slope > 0:
        raise ValueError(
            "Slope > 0, make sure slope is in negative units in elevation drop/ unit in length"
        )
    # calculating cross section for half full flow:
    A = 0.5 * math.pi * (pipe_diameter / 2) ** 2
    # wetted perimeter
    P = 0.5 * 2 * math.pi * pipe_diameter / 2
    # hydraulic radius
    Rh = A / P
    # cross sectional mean velocity
    v = (1 / roughness) * Rh ** (2 / 3) * (-1 * slope) ** 0.5
    # volume_flow
    q = A * v
    return q


def select_diameter(
    target_flow: float, diameters: List[float], roughness: float, slope: float
):
    """
    Returns the minimum pipe diameter.

    Parameters
    ----------
    target_flow : float
        The target flow rate in cubic meters per second.
    diameters : list
        A list of possible pipe diameters in meters.
    roughness : float
        The pipe roughness coefficient in meters.
    slope : float
        The pipe slope in meters per meter.

    Returns
    -------
    float
        The minimum pipe diameter required to achieve the target flow rate.

    Raises
    ------
    ValueError
        If the maximum diameter is insufficient to reach the target flow rate.
    """
    diameters = diameters.copy()
    flow = 0
    while flow <= target_flow:
        try:
            selected_diameter = diameters.pop(0)
        except Exception:
            raise ValueError("Maximum Diameter insufficient to reach target flow")
        flow = mannings_equation(selected_diameter, roughness, slope)
    return selected_diameter


def needs_pump(
    profile,
    min_slope: float = None,
    tmax: float = None,
    tmin: float = None,
    inflow_trench_depth: float = None,
):
    """
    Traces a profile to determine if gravitational flow can be achieved within slope and trench depth constraints.

    Parameters
    ----------
    profile : list of tuples
        A list of (x, y) tuples representing the profile to be traced.
    min_slope : float, optional
        The minimum slope required for gravitational flow. Default is -0.01.
    tmax : float, optional
        The maximum trench depth allowed. Default is 8.
    tmin : float, optional
        The minimum trench depth allowed. Default is 0.25.
    inflow_trench_depth : float, optional
        The trench depth at the inflow point. If not specified, it is set to tmin.

    Returns
    -------
    tuple
        A tuple containing:
        - A boolean indicating whether a pump is needed.
        - The height difference between the outflow and the trench depth at the outflow point.
        - A list of (x, trench_depth) tuples representing the trench depth at each point along the profile.
    """
    # Get current config values if not provided
    config = get_config()
    min_slope = min_slope or config.optimization.min_slope
    tmax = tmax or config.optimization.tmax
    tmin = tmin or config.optimization.tmin
    inflow_trench_depth = inflow_trench_depth or config.optimization.inflow_trench_depth

    x, y = zip(*profile)
    if inflow_trench_depth == 0:
        inflow_trench_depth = tmin
        logger.info(f"  Using tmin as inflow_trench_depth: {inflow_trench_depth}")

    trench_depth = [0] * len(x)
    # adjust inflow trenchdepth
    trench_depth[0] = np.array(y)[0] - inflow_trench_depth
    logger.info(f"  Initial trench depth: {trench_depth[0]}")

    for i in range(len(x) - 1):
        dx = x[i + 1] - x[i]
        next_min_elevation = dx * min_slope + trench_depth[i]
        logger.info(f"\nChecking segment {i} to {i+1}:")
        logger.info(f"  Distance: {dx}")
        logger.info(f"  Current elevation: {y[i]}")
        logger.info(f"  Next elevation: {y[i+1]}")
        logger.info(f"  Required next_min_elevation: {next_min_elevation}")
        logger.info(f"  Max allowed depth: {y[i+1] - tmax}")
        logger.info(f"  Min allowed depth: {y[i+1] - tmin}")
        ## Case 1: to achieve the min slope we would need to dig a trench deeper than tmax -> needs pump
        if next_min_elevation < y[i + 1] - tmax:
            logger.info("  CASE 1: Pump needed - trench would be too deep")
            logger.info(f"  Would need depth: {y[i+1] - next_min_elevation}")
            logger.info(f"  Max allowed: {tmax}")
            return (
                True,
                0,
                [
                    (
                        0,
                        tmin,
                    ),
                    (x[-1], tmin),
                ],
            )
        ## case 2: min slope point is within Trench depth range: set trench depth to calculated height
        elif (
            next_min_elevation < y[i + 1] - tmin
            and next_min_elevation > y[i + 1] - tmax
        ):
            trench_depth[i + 1] = next_min_elevation
        ##case 3: min slope point is higher than min trenchdepth: set to tmin
        elif next_min_elevation >= y[i + 1] - tmin:
            trench_depth[i + 1] = y[i + 1] - tmin
    logger.info("\nFinal result: No pump needed")
    logger.info(f"  Final trench depth: {trench_depth[-1]}")
    logger.info(f"  Final elevation difference: {y[-1] - trench_depth[-1]}")
    return (False, y[-1] - trench_depth[-1], list(zip(x, trench_depth)))
