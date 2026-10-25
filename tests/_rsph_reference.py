# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""Frozen copy of ``rsph_tree`` from pysewer 0.3.1 (all-pairs Dijkstra).

Reference for ``tests/test_rsph_memory.py``: the memory-lean ``rsph_tree``
must return exactly this layout. Do not edit.
"""

import itertools

import networkx as nx

from pysewer.helper import get_node_keys
from pysewer.routing import NodeType, _report_unrouted


def rsph_tree_v031(
    connection_graph: nx.Graph,
    sinks: list[NodeType],
    from_type: str = "building",
    skip_nodes: list[NodeType] | None = None,
    *,
    strict: bool = False,
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
    strict : bool, optional, keyword-only
        Raise :class:`UnroutedTerminalsError` (a ``ValueError``) if any terminal
        cannot reach a sink, by default False.

    Returns
    -------
    nx.DiGraph
        The directed routed steiner tree that connects all terminal nodes to the sink using the repeated shortest path heuristic.
        Terminals without a path to any sink stay in the graph as isolated
        nodes; they are listed in ``graph["unrouted_terminals"]`` (empty list
        if every terminal is connected) and a warning is issued.
    """
    # Create a new DiGraph with the same nodes and attributes as connection_graph
    if skip_nodes is None:
        skip_nodes = []
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

    # Keep the components that contain a sink; terminals elsewhere cannot be
    # routed and are reported instead of being dropped silently
    unrouted = []
    if not nx.is_weakly_connected(connection_graph):
        sink_set = set(sinks)
        keep = set().union(*(c for c in wcc if c & sink_set))
        connection_graph = connection_graph.subgraph(keep).copy()
        unrouted = [t for t in terminals if t not in keep]
        terminals = [t for t in terminals if t in keep]
        subgraph_nodes = [s for s in subgraph_nodes if s in keep]

    all_paths = dict(nx.all_pairs_dijkstra_path(connection_graph))
    all_lengths = dict(nx.all_pairs_dijkstra_path_length(connection_graph))

    print(f"Number of terminals (buildings) to process: {len(terminals)}")

    while terminals:
        try:
            path = _find_rsph_path_v031(
                connection_graph, subgraph_nodes, terminals, all_paths, all_lengths
            )
        except ValueError as e:
            print(f"Error finding path: {e}")
            unrouted.extend(terminals)
            break  # exit the loop if no viable terminal is found

        # Add edges of path to sewer graph while keeping edge attributes
        # and propagate sink node in sewer graph attributes (edges and nodes)
        nx.add_path(sewer_graph, path)

        edgesinpath = itertools.pairwise(path)
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

    _report_unrouted(sewer_graph, unrouted, from_type, strict)
    return sewer_graph


def _find_rsph_path_v031(
    connection_graph: nx.Graph,
    subgraph_nodes: list[NodeType],
    terminals: list[NodeType],
    all_paths: dict,
    all_lengths: dict,
) -> list[NodeType]:
    # (distance, terminal, tree node) per terminal that reaches the tree;
    # terminals that reach no tree node are skipped without shifting the others
    candidates = []
    for terminal in terminals:
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
        candidates.append((min_dist, terminal, tree_node))

    if not candidates:
        raise ValueError(
            f"No viable terminal found to connect to the subgraph nodes: {subgraph_nodes}."
        )
    # first terminal with the smallest distance (same tie-break as before)
    _, closest_terminal, tree_node = min(candidates, key=lambda c: c[0])

    if tree_node not in all_paths[closest_terminal]:
        raise ValueError(
            f"No recorded path from {closest_terminal} to {tree_node} in the all_paths dictionary."
        )
    return all_paths[closest_terminal][tree_node]
