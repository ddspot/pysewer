# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

from typing import Hashable, List, Tuple, Union

import networkx as nx

from .helper import get_node_keys

NodeType = Union[int, str, Hashable]

NodeType_t = Tuple[float, float]


def rsph_tree_fast(
    connection_graph: nx.Graph,
    sink: List[NodeType_t],
    from_type: str = "building",
) -> nx.DiGraph:
    """
    Returns the directed routed steiner tree that connects all terminal nodes to the sink using the repeated shortest path heuristic.

    Parameters:
    -----------
    connection_graph : nx.Graph
        The graph representing the sewer network.
    sink : List[Tuple[float, float]]
        The coordinates of the sink node.
    from_type : str, optional
        The type of the source nodes, by default "building".
    to_type : str, optional
        The type of the destination nodes, by default "wwtp".

    Returns:
    --------
    nx.DiGraph
        A directed graph representing the routed steiner tree that connects all terminal nodes to the sink.
    """
    sewer_graph = nx.DiGraph(nx.create_empty_copy(connection_graph))
    terminals = get_node_keys(connection_graph, field="node_type", value=from_type)
    subgraph_nodes = sink
    while len(terminals) > 0:
        try:
            path = nx.dijkstra_path(
                connection_graph, terminals[0], sink, weight="weight"
            )
            # add edges of path to sewer graph while keeping edge attributes
            nx.add_path(sewer_graph, path)

            edgesinpath = zip(path[0:], path[1:])
            for e in edgesinpath:
                nx.set_edge_attributes(
                    sewer_graph,
                    {(e[0], e[1]): connection_graph.get_edge_data(e[0], e[1])[0]},
                )
            subgraph_nodes = subgraph_nodes + path[1:-1]
        except:
            print("WARNING: no Path from Building " + str(terminals[0]) + "to sink")

        terminals.remove(terminals[0])
    return sewer_graph


def rsph_tree(
    connection_graph: nx.Graph,
    sinks: List[NodeType],
    from_type: str = "building",
    skip_nodes: List[NodeType] = [],
) -> nx.DiGraph:
    """
    Returns the directed routed steiner tree that connects all terminal nodes to the sink using the repeated shortest path heuristic.

    Parameters
    ----------
    connection_graph : nx.Graph
        The graph representing the sewer network.
    sinks : List[NodeType]
        The nodes representing the sinks in the sewer network.
    from_type : str, optional
        The type of the source nodes, by default "building".
    to_type : str, optional
        The type of the sink nodes, by default "wwtp".
    skip_nodes : List[NodeType], optional
        The nodes to skip in the routing, by default [].

    Returns
    -------
    nx.DiGraph
        The directed routed steiner tree that connects all terminal nodes to the sink using the repeated shortest path heuristic.
    """
    # Create a new DiGraph with the same nodes and attributes as connection_graph
    sewer_graph = nx.DiGraph(nx.create_empty_copy(connection_graph))
    terminals = get_node_keys(connection_graph, field="node_type", value=from_type)
    terminals = [n for n in terminals if n not in skip_nodes]
    subgraph_nodes = list(set(sinks))  # Remove duplicates

    # Print number of weakly connected components
    wcc = list(nx.weakly_connected_components(connection_graph))
    print(f"Number of weakly connected components: {len(wcc)}")

    # TODO: add virtual path, adressing the issue of multiple components in the graph slows down the computation, especially for larger networks. need to be tested
    # If there are multiple components, try to connect them
    # if len(wcc) > 1:
    #     print("Attempting to connect components...")
    #     for i in range(len(wcc) - 1):
    #         source = next(iter(wcc[i]))
    #         target = next(iter(wcc[i+1]))
    #         try:
    #             path = nx.shortest_path(connection_graph, source, target)
    #             nx.add_path(sewer_graph, path)
    #             print(f"Connected component {i} to {i+1}")
    #         except nx.NetworkXNoPath:
    #             print(f"No path found between components {i} and {i+1}.")
    # print(f"No path found between components {i} and {i+1}. Adding virtual path.")
    # add_virtual_path(sewer_graph, source, target)

    # Safeguard against sinks not being in the connection graph
    for sink in sinks:
        if sink not in connection_graph.nodes:
            raise ValueError(f"Sink {sink} not found in the connection graph.")

    # Handling single sink case
    if len(subgraph_nodes) == 1:
        subgraph_nodes = subgraph_nodes * 2

    # Ensure the graph is weakly connected
    if not nx.is_weakly_connected(connection_graph):
        largest_wcc = max(nx.weakly_connected_components(connection_graph), key=len)
        connection_graph = connection_graph.subgraph(largest_wcc).copy()
        terminals = [t for t in terminals if t in largest_wcc]
        subgraph_nodes = [s for s in subgraph_nodes if s in largest_wcc]

    all_paths = dict(nx.all_pairs_dijkstra_path(connection_graph))
    all_lengths = dict(nx.all_pairs_dijkstra_path_length(connection_graph))

    print(f"Number of terminals (buildings) to process: {len(terminals)}")

    while terminals:
        try:
            path = find_rsph_path(
                connection_graph, subgraph_nodes, terminals, all_paths, all_lengths
            )
        except ValueError as e:
            print(f"Error finding path: {e}")
            break  # exit the loop if no viable terminal is found

        # Add edges of path to sewer graph while keeping edge attributes
        # and propagate sink node in sewer graph attributes (edges and nodes)
        nx.add_path(sewer_graph, path)

        edgesinpath = zip(path[:-1], path[1:])
        for edge in edgesinpath:
            attrs = connection_graph.get_edge_data(edge[0], edge[1])[0]
            path_dest_node = path[-1]
            try:
                sink_node = str(sewer_graph.nodes(data=True)[path_dest_node]["sink_coords"])
            except KeyError:
                sink_node = path_dest_node

            attrs["sink_coords"] = str(sink_node)
            nx.set_node_attributes(
                sewer_graph,
                {edge_node: {"sink_coords": str(sink_node)} for edge_node in edge}
            )
            nx.set_edge_attributes(
                sewer_graph, {(edge[0], edge[1]): attrs},
            )
        subgraph_nodes.extend(path[1:-1])
        subgraph_nodes = list(set(subgraph_nodes))  # Remove duplicates
        terminals.remove(path[0])

    print(f"Number of nodes in final sewer graph: {sewer_graph.number_of_nodes()}")
    print(f"Number of edges in final sewer graph: {sewer_graph.number_of_edges()}")

    return sewer_graph


def find_rsph_path(
    connection_graph: nx.Graph,
    subgraph_nodes: List[NodeType],
    terminals: List[NodeType],
    all_paths: dict,
    all_lengths: dict,
) -> List[NodeType]:
    terminal_distances = []
    closest_tree_node = []
    for terminal in terminals:
        missing_nodes = [
            node for node in subgraph_nodes if node not in all_lengths[terminal]
        ]
        if missing_nodes:
            print(f"Terminal {terminal} missing distances to nodes: {missing_nodes}.")
            continue

        distances = [
            all_lengths[terminal][node]
            for node in subgraph_nodes
            if node in all_lengths[terminal]
        ]
        if not distances:
            continue  # Skip this terminal if no valid distances are found

        min_dist = min(distances)
        tree_node = subgraph_nodes[
            subgraph_nodes.index(
                next(
                    node
                    for node in subgraph_nodes
                    if node in all_lengths[terminal]
                    and all_lengths[terminal][node] == min_dist
                )
            )
        ]
        terminal_distances.append(min_dist)
        closest_tree_node.append(tree_node)

    if not terminal_distances:
        raise ValueError(
            f"No viable terminal found to connect to the subgraph nodes: {subgraph_nodes}."
        )
    min_dist_global = min(terminal_distances)
    result_index = terminal_distances.index(min_dist_global)
    closest_terminal = terminals[result_index]
    tree_node = closest_tree_node[result_index]

    if tree_node not in all_paths[closest_terminal]:
        raise ValueError(
            f"No recorded path from {closest_terminal} to {tree_node} in the all_paths dictionary."
        )
    return all_paths[closest_terminal][tree_node]


# def add_virtual_path(graph: nx.DiGraph, source: Tuple, target: Tuple) -> None:
#     """
#     Adds a virtual path between two nodes in the graph.
#     """
#     midpoint = ((source[0] + target[0]) / 2, (source[1] + target[1]) / 2)
#     graph.add_edge(source, midpoint, virtual=True, weight=1000)  # High weight for virtual edges
#     graph.add_edge(midpoint, target, virtual=True, weight=1000)
#     print(f"Added virtual path between {source} and {target}")
