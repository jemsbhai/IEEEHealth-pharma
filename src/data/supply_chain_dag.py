"""
Supply Chain DAG — Topology model for pharmaceutical supply chains.

Implements Definition 1 from [Paper §3.1]: a directed acyclic graph
G = (N, E) where nodes are custody points and edges are custody transfers.

This is a lightweight, domain-specific DAG — not a general graph library.
It provides topology construction, path enumeration, and integration with
the chain integrity algebra (chain_integrity.py).

Design choice: we use a simple adjacency-list DAG rather than the full
SLNetwork from jsonld-ex because:
1. Our contribution is the domain formalization, not graph infrastructure
2. A self-contained model is easier for reviewers to verify
3. We compose SL operators (from jsonld-ex) over this topology; we don't
   need SLNetwork's built-in inference engine
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Set, Tuple


class NodeType(Enum):
    """Types of nodes in the pharmaceutical supply chain."""
    MANUFACTURER = auto()
    IMPORTER = auto()
    WAREHOUSE = auto()
    DISTRIBUTOR = auto()
    PHARMACY = auto()


@dataclass
class SupplyChainNode:
    """A node (custody point) in the supply chain DAG.

    Attributes:
        node_id:  Unique identifier.
        node_type: Functional role in the supply chain.
        label:    Human-readable description.
        metadata: Optional domain metadata (e.g., cold chain zones,
                  GLN, Tatmeen registration status).
    """
    node_id: str
    node_type: NodeType
    label: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SupplyChainEdge:
    """An edge (custody transfer) in the supply chain DAG.

    Attributes:
        source:    Source node ID.
        target:    Target node ID.
        transit_hours_range: Expected transit duration range [min, max] in hours.
    """
    source: str
    target: str
    transit_hours_range: Tuple[float, float] = (0.0, 0.0)


class SupplyChainDAG:
    """Directed acyclic graph representing a pharmaceutical supply chain.

    Provides:
    - Node and edge management with cycle detection
    - Source/sink identification (manufacturers and pharmacies)
    - Path enumeration (all source-to-sink product paths)
    - Topological ordering

    Per Definition 1 (Paper §3.1):
        G = (N, E) where N = {n_1, ..., n_k} are custody nodes and
        E ⊆ N × N are custody transfer edges.
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, SupplyChainNode] = {}
        self._edges: List[SupplyChainEdge] = []
        self._adjacency: Dict[str, List[str]] = {}    # forward edges
        self._reverse_adj: Dict[str, List[str]] = {}   # backward edges

    # ── Construction ───────────────────────────────────────────

    def add_node(self, node: SupplyChainNode) -> None:
        """Add a node to the DAG."""
        if node.node_id in self._nodes:
            raise ValueError(f"Node {node.node_id!r} already exists")
        self._nodes[node.node_id] = node
        self._adjacency.setdefault(node.node_id, [])
        self._reverse_adj.setdefault(node.node_id, [])

    def add_edge(self, edge: SupplyChainEdge) -> None:
        """Add an edge to the DAG. Raises ValueError if it creates a cycle."""
        if edge.source not in self._nodes:
            raise ValueError(f"Source node {edge.source!r} not found")
        if edge.target not in self._nodes:
            raise ValueError(f"Target node {edge.target!r} not found")

        # Cycle detection: check if target can reach source
        if self._can_reach(edge.target, edge.source):
            raise ValueError(
                f"Edge {edge.source} → {edge.target} would create a cycle"
            )

        self._edges.append(edge)
        self._adjacency[edge.source].append(edge.target)
        self._reverse_adj[edge.target].append(edge.source)

    def _can_reach(self, start: str, target: str) -> bool:
        """Check if target is reachable from start via DFS."""
        visited: Set[str] = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node in visited:
                continue
            visited.add(node)
            stack.extend(self._adjacency.get(node, []))
        return False

    # ── Queries ────────────────────────────────────────────────

    def get_node(self, node_id: str) -> SupplyChainNode:
        """Get a node by ID."""
        if node_id not in self._nodes:
            raise KeyError(f"Node {node_id!r} not found")
        return self._nodes[node_id]

    @property
    def nodes(self) -> List[SupplyChainNode]:
        """All nodes in insertion order."""
        return list(self._nodes.values())

    @property
    def edges(self) -> List[SupplyChainEdge]:
        """All edges."""
        return list(self._edges)

    @property
    def node_ids(self) -> List[str]:
        """All node IDs."""
        return list(self._nodes.keys())

    def sources(self) -> List[str]:
        """Nodes with no incoming edges (manufacturers)."""
        return [
            nid for nid in self._nodes
            if len(self._reverse_adj.get(nid, [])) == 0
        ]

    def sinks(self) -> List[str]:
        """Nodes with no outgoing edges (pharmacies/hospitals)."""
        return [
            nid for nid in self._nodes
            if len(self._adjacency.get(nid, [])) == 0
        ]

    def predecessors(self, node_id: str) -> List[str]:
        """Direct predecessors (upstream nodes)."""
        return list(self._reverse_adj.get(node_id, []))

    def successors(self, node_id: str) -> List[str]:
        """Direct successors (downstream nodes)."""
        return list(self._adjacency.get(node_id, []))

    def get_edge(self, source: str, target: str) -> Optional[SupplyChainEdge]:
        """Get edge between two nodes, if it exists."""
        for e in self._edges:
            if e.source == source and e.target == target:
                return e
        return None

    # ── Path Enumeration ───────────────────────────────────────

    def enumerate_paths(
        self,
        source: Optional[str] = None,
        sink: Optional[str] = None,
    ) -> List[List[str]]:
        """Enumerate all directed paths from source(s) to sink(s).

        Per Definition 1: a product path π = (n_1, ..., n_m) is a
        source-to-sink path representing the complete journey of a
        batch from manufacture to dispensing.

        Args:
            source: Start node (default: all sources).
            sink:   End node (default: all sinks).

        Returns:
            List of paths, each path being a list of node IDs.
        """
        sources = [source] if source else self.sources()
        sinks = set([sink] if sink else self.sinks())

        all_paths: List[List[str]] = []

        for src in sources:
            # DFS to find all paths from src to any sink
            stack: List[Tuple[str, List[str]]] = [(src, [src])]
            while stack:
                node, path = stack.pop()
                if node in sinks:
                    all_paths.append(path)
                    continue
                for neighbor in self._adjacency.get(node, []):
                    stack.append((neighbor, path + [neighbor]))

        return all_paths

    def topological_sort(self) -> List[str]:
        """Kahn's algorithm with deterministic tie-breaking."""
        in_degree = {nid: 0 for nid in self._nodes}
        for e in self._edges:
            in_degree[e.target] += 1

        # Use sorted() for deterministic ordering
        queue = sorted([nid for nid, deg in in_degree.items() if deg == 0])
        result: List[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for neighbor in sorted(self._adjacency.get(node, [])):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
                    queue.sort()

        if len(result) != len(self._nodes):
            raise RuntimeError("Cycle detected during topological sort")

        return result

    # ── Factory ────────────────────────────────────────────────

    @classmethod
    def from_config(cls, config: dict) -> SupplyChainDAG:
        """Build a DAG from a YAML config dict (configs/base.yaml format).

        Expected structure:
            topology:
              nodes:
                - {id: "...", type: "MANUFACTURER", label: "..."}
              edges:
                - ["source_id", "target_id"]
        """
        dag = cls()
        topo = config.get("topology", config)

        for node_cfg in topo["nodes"]:
            dag.add_node(SupplyChainNode(
                node_id=node_cfg["id"],
                node_type=NodeType[node_cfg["type"]],
                label=node_cfg.get("label", ""),
            ))

        for edge_cfg in topo["edges"]:
            source, target = edge_cfg[0], edge_cfg[1]
            dag.add_edge(SupplyChainEdge(source=source, target=target))

        return dag

    def __repr__(self) -> str:
        return (
            f"SupplyChainDAG(nodes={len(self._nodes)}, "
            f"edges={len(self._edges)}, "
            f"paths={len(self.enumerate_paths())})"
        )
