# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

import itertools

import networkx as nx
from shapely.geometry import LineString, Point

## Code Adapted from:
# Boeing, G. 2017. "OSMnx: New Methods for Acquiring, Constructing, Analyzing, and Visualizing Complex Street Networks." Computers, Environment and Urban Systems 65, 126-139. doi:10.1016/j.compenvurbsys.2017.05.004


def simplify_graph(
    G: nx.MultiDiGraph, strict: bool = True, remove_rings: bool = True
) -> nx.MultiDiGraph:
    """
    Simplify a graph's topology by removing interstitial nodes.
    Simplify graph topology by removing all nodes that are not intersections
    or dead-ends. Create an edge directly between the end points that
    encapsulate them, but retain the geometry of the original edges, saved as
    an attribute in new edge. Some of the resulting consolidated edges may
    comprise multiple OSM ways, and if so, their multiple attribute values are
    stored as a list.
    Parameters
    ----------
    G : networkx.MultiDiGraph
        input graph
    strict : bool
        if False, allow nodes to be end points even if they fail all other
        rules but have incident edges with different OSM IDs. Lets you keep
        nodes at elbow two-way intersections, but sometimes individual blocks
        have multiple OSM IDs within them too.
    remove_rings : bool
        if True, remove isolated self-contained rings that have no endpoints
    Returns
    -------
    G : networkx.MultiDiGraph
        topologically simplified graph

    """
    # if _is_simplified(G):
    #    raise Exception("This graph has already been simplified, cannot simplify it again.")

    # utils.log("Begin topologically simplifying the graph...")

    # make a copy to not edit the original graph object the caller passed in
    G = G.copy()
    initial_node_count = len(G)
    initial_edge_count = len(G.edges)
    all_nodes_to_remove = []
    all_edges_to_add = []

    # generate each path that needs to be simplified
    for path in _get_paths_to_simplify(G):
        # add the interstitial edges we're removing to a list so we can retain
        # their spatial geometry
        edge_attributes = dict()
        for u, v in itertools.pairwise(path):
            # there should rarely be multiple edges between interstitial nodes
            # usually happens if OSM has duplicate ways digitized for just one
            # street... we will keep only one of the edges (see below)
            if G.number_of_edges(u, v) != 1:
                # utils.log(f"Found multiple edges between {u} and {v} when simplifying")
                pass
            # get edge between these nodes: if multiple edges exist between
            # them (see above), we retain only one in the simplified graph
            edge = G.edges[u, v]
            for key in edge:
                if key in edge_attributes:
                    # if this key already exists in the dict, append it to the
                    # value list
                    edge_attributes[key].append(edge[key])
                else:
                    # if this key doesn't already exist, set the value to a list
                    # containing the one value
                    edge_attributes[key] = [edge[key]]

        for key in edge_attributes:
            # don't touch the length attribute, we'll sum it at the end
            if len(set(edge_attributes[key])) == 1 and key != "length":
                # if there's only 1 unique value in this attribute list,
                # consolidate it to the single value (the zero-th)
                edge_attributes[key] = edge_attributes[key][0]
            elif key != "length":
                # otherwise, if there are multiple values, keep one of each value
                edge_attributes[key] = list(set(edge_attributes[key]))

        # construct the geometry and sum the lengths of the segments
        edge_attributes["geometry"] = LineString([Point(node) for node in path])
        edge_attributes["length"] = edge_attributes["geometry"].length

        # add the nodes and edges to their lists for processing at the end
        all_nodes_to_remove.extend(path[1:-1])
        all_edges_to_add.append(
            {"origin": path[0], "destination": path[-1], "attr_dict": edge_attributes}
        )

    # for each edge to add in the list we assembled, create a new edge between
    # the origin and destination
    for edge in all_edges_to_add:
        G.add_edge(edge["origin"], edge["destination"])

    # finally remove all the interstitial nodes between the new edges
    G.remove_nodes_from(set(all_nodes_to_remove))

    # mark graph as having been simplified
    G.graph["simplified"] = True

    msg = (
        f"Simplified graph: {initial_node_count} to {len(G)} nodes, "
        f"{initial_edge_count} to {len(G.edges)} edges"
    )
    print(msg)
    return G


def _get_paths_to_simplify(G):
    """
    Generate all the paths to be simplified between endpoint nodes.
    The path is ordered from the first endpoint, through the interstitial nodes,
    to the second endpoint.
    Parameters
    ----------
    G : networkx.MultiDiGraph
        input graph
    strict : bool
        if False, allow nodes to be end points even if they fail all other rules
        but have edges with different OSM IDs
    Returns
    ------
    path_to_simplify : list
    """
    # first identify all the nodes that are endpoints
    endpoints = get_essential_nodes(G)

    # for each endpoint node, look at each of its successor nodes
    for endpoint in endpoints:
        for successor in G.neighbors(endpoint):
            if successor not in endpoints:
                # if endpoint node's successor is not an endpoint, build a path
                # from the endpoint node, through the successor, and on to the
                # next endpoint node
                yield _build_path(G, endpoint, successor, endpoints)


def _build_path(G, endpoint, endpoint_successor, endpoints):
    """
    Build a path of nodes from one endpoint node to next endpoint node.
    Parameters
    ----------
    G : networkx.MultiDiGraph
        input graph
    endpoint : int
        the endpoint node from which to start the path
    endpoint_successor : int
        the successor of endpoint through which the path to the next endpoint
        will be built
    endpoints : set
        the set of all nodes in the graph that are endpoints
    Returns
    -------
    path : list
        the first and last items in the resulting path list are endpoint
        nodes, and all other items are interstitial nodes that can be removed
        subsequently
    """
    # start building path from endpoint node through its successor
    path = [endpoint, endpoint_successor]

    # for each successor of the endpoint's successor
    for successor in G.neighbors(endpoint_successor):
        if successor not in path:
            # if this successor is already in the path, ignore it, otherwise add
            # it to the path
            path.append(successor)
            while successor not in endpoints:
                # find successors (of current successor) not in path
                successors = [n for n in G.neighbors(successor) if n not in path]

                # 99%+ of the time there will be only 1 successor: add to path
                if len(successors) == 1:
                    successor = successors[0]
                    path.append(successor)

                # handle relatively rare cases or OSM digitization quirks
                elif len(successors) == 0:
                    if endpoint in G.neighbors(successor):
                        # we have come to the end of a self-looping edge, so
                        # add first node to end of path to close it and return
                        return [*path, endpoint]
                    else:
                        # this can happen due to OSM digitization error where
                        # a one-way street turns into a two-way here, but
                        # duplicate incoming one-way edges are present
                        # utils.log(
                        #    f"Unexpected simplify pattern handled near {successor}", level=lg.WARN
                        # )
                        return path
                else:
                    # if successor has >1 successors, then successor must have
                    # been an endpoint because you can go in 2 new directions.
                    # this should never occur in practice
                    raise Exception(
                        f"Unexpected simplify pattern failed near {successor}"
                    )

            # if this successor is an endpoint, we've completed the path
            return path

    # if endpoint_successor has no successors not already in the path, return
    # the current path: this is usually due to a digitization quirk on OSM
    return path


def get_essential_nodes(G):
    """
    Returns a list of essential nodes to build the simplified graph. This includes all junctions (degree > 2), buildings, and connection points.

    Parameters
    ----------
    G : networkx.Graph
        NetworkX street graph with connected buildings.

    Returns
    -------
    List
        List of node keys.

    Examples
    --------
    >>> G = nx.Graph()
    >>> G.add_node(1, road_network=True)
    >>> G.add_node(2, road_network=True)
    >>> G.add_node(3, road_network=False)
    >>> G.add_node(4, connection_node=True)
    >>> G.add_node(5, connection_node=False)
    >>> G.add_edge(1, 2)
    >>> G.add_edge(2, 3)
    >>> G.add_edge(2, 4)
    >>> G.add_edge(4, 5)
    >>> get_essential_nodes(G)
    [1, 2, 3, 5]

    """
    connection_nodes = [
        x for x, y in G.nodes(data="connection_node", default=False) if y
    ]
    contract_nodes = [
        n for n in G.nodes if G.degree(n) == 2 and n not in connection_nodes
    ]
    print(len(G.nodes))
    de_nodes = [
        x
        for x, y in G.nodes(data="road_network", default=False)
        if y and G.degree(x) == 1
    ]

    essential_nodes = [n for n in G.nodes if n not in contract_nodes] + de_nodes
    print(len(essential_nodes))

    return essential_nodes
