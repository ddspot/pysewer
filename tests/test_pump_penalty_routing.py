# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

import networkx as nx

from pysewer.routing import rsph_tree_fast


def test_pump_penalty_influences_shortest_path():
    """
    A pumped edge gets its weight multiplied by pump_penalty, steering routing away from it.
    """
    pump_penalty = 10
    G = nx.Graph()
    # Node types used by routing to find terminals/sinks
    G.add_node("b1", node_type="building")
    G.add_node("mid", node_type="road")
    G.add_node("sink", node_type="wwtp")

    # Direct path that needs a pump (short distance but heavily penalized)
    G.add_edge(
        "b1",
        "sink",
        distance=10,
        needs_pump=True,
        weight=10 * pump_penalty,
    )
    # Alternative gravity path (longer distance but no pump)
    G.add_edge("b1", "mid", distance=30, needs_pump=False, weight=30)
    G.add_edge("mid", "sink", distance=30, needs_pump=False, weight=30)

    # Verify the weighted shortest path avoids the pumped edge
    path = nx.dijkstra_path(G, "b1", "sink", weight="weight")
    assert path == ["b1", "mid", "sink"]

    # Routing helper should respect the same weighting
    sewer = rsph_tree_fast(G, sink="sink")
    assert ("b1", "mid") in sewer.edges()
    assert ("mid", "sink") in sewer.edges()
    assert ("b1", "sink") not in sewer.edges()
