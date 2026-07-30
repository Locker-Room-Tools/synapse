"""Bounded, deterministic breadth-first traversal over stored index relations."""

from dataclasses import dataclass, field
from enum import StrEnum

from synapse.core.index import ReadProjections
from synapse.core.models import Confidence, Relation, RelationKind, ResolutionMethod, Symbol

TRAVERSAL_KINDS: tuple[RelationKind, ...] = (RelationKind.CONTAINS, RelationKind.REFERENCES)

MIN_DEPTH = 1
MAX_DEPTH = 5
MAX_NULL_TARGET_REFS = 25


class Direction(StrEnum):
    """Which stored edges to follow relative to the frontier."""

    IN = "in"
    OUT = "out"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class TraversalLimits:
    """Structural guards bounding one traversal."""

    max_depth: int = 3
    max_nodes: int = 100
    max_edges_per_node: int = 20
    max_total_edges: int = 400

    def clamped(self) -> "TraversalLimits":
        """Clamp every guard to a safe positive range."""
        return TraversalLimits(
            max_depth=min(MAX_DEPTH, max(MIN_DEPTH, self.max_depth)),
            max_nodes=max(1, self.max_nodes),
            max_edges_per_node=max(1, self.max_edges_per_node),
            max_total_edges=max(1, self.max_total_edges),
        )


@dataclass(frozen=True, slots=True)
class TraversedEdge:
    """One stored relation crossed at a known depth and direction."""

    relation: Relation
    depth: int
    direction: str


@dataclass(frozen=True, slots=True)
class TraversedNode:
    """One discovered symbol with its discovery-tree parent edge."""

    symbol: Symbol
    depth: int
    parent_edge_id: str | None


@dataclass(frozen=True, slots=True)
class TraversalOutcome:
    """Everything one bounded traversal discovered, guards included."""

    nodes: dict[str, TraversedNode]
    edges: tuple[TraversedEdge, ...]
    suppressed: dict[str, int]
    guards: dict[str, bool]
    unresolved_edges: int
    dangling_targets: int
    depth_reached: int
    frontier_remaining: int
    heuristic_leaf_edges: int
    null_target_references: tuple[Relation, ...]


_KIND_RANKS: dict[RelationKind, int] = {
    RelationKind.CONTAINS: 0,
    RelationKind.REFERENCES: 1,
}
_RESOLUTION_RANKS: dict[ResolutionMethod, int] = {
    ResolutionMethod.EXACT: 0,
    ResolutionMethod.SCOPED: 1,
    ResolutionMethod.UNIQUE_NAME: 2,
    ResolutionMethod.AMBIGUOUS: 4,
    ResolutionMethod.UNRESOLVED: 5,
}
_UNRANKED_RESOLUTION = 3
_CONFIDENCE_RANKS: dict[Confidence, int] = {
    Confidence.HIGH: 0,
    Confidence.MEDIUM: 1,
    Confidence.LOW: 2,
}


def resolution_rank(resolution: ResolutionMethod | None) -> int:
    """Rank a stored resolution method for ordering; never changes the stored value."""
    if resolution is None:
        return _UNRANKED_RESOLUTION
    return _RESOLUTION_RANKS[resolution]


def confidence_rank(confidence: Confidence) -> int:
    """Rank a stored confidence for ordering; never changes the stored value."""
    return _CONFIDENCE_RANKS[confidence]


def edge_trust(relation: Relation) -> str:
    """Classify a stored edge for traversal policy: exact, scoped, or heuristic.

    Containment is a parser-proven structural fact and counts as exact. Reference
    edges keep their stored resolution: exact and scoped are trustworthy transit;
    everything weaker (unique-name and unclassified) is heuristic.
    """
    if relation.kind is RelationKind.CONTAINS:
        return "exact"
    if relation.resolution is ResolutionMethod.EXACT:
        return "exact"
    if relation.resolution is ResolutionMethod.SCOPED:
        return "scoped"
    return "heuristic"


def is_transit_edge(relation: Relation) -> bool:
    """Whether traversal may continue past this edge (trust policy).

    Heuristic edges are recorded as leaf evidence but never used as transit, so
    symbols cannot be chained merely because they share a common name.
    """
    return edge_trust(relation) != "heuristic"


def _edge_sort_key(relation: Relation) -> tuple[int, int, int, int, str]:
    return (
        _KIND_RANKS[relation.kind],
        resolution_rank(relation.resolution),
        _CONFIDENCE_RANKS[relation.confidence],
        relation.start_line or 0,
        relation.id,
    )


@dataclass(slots=True)
class _TraversalState:
    nodes: dict[str, TraversedNode]
    edges: list[TraversedEdge]
    seen_edge_ids: set[str]
    suppressed: dict[str, int]
    guards: dict[str, bool]
    unresolved_edges: int = 0
    dangling_targets: int = 0
    depth_reached: int = 0
    heuristic_leaf_edges: int = 0
    null_target_references: list[Relation] = field(default_factory=list)


def _group_by_endpoint(relations: list[Relation], *, endpoint: str) -> dict[str, list[Relation]]:
    grouped: dict[str, list[Relation]] = {}
    for relation in relations:
        key = relation.from_symbol_id if endpoint == "from" else relation.to_symbol_id
        if key is not None:
            grouped.setdefault(key, []).append(relation)
    return grouped


def _expand_level(
    state: _TraversalState,
    frontier: list[str],
    outgoing: dict[str, list[Relation]],
    incoming: dict[str, list[Relation]],
    depth: int,
    limits: TraversalLimits,
) -> list[tuple[str, TraversedEdge, bool]]:
    """Cross one BFS level; returns (far id, discovering edge, transit) in order."""
    discovered: list[tuple[str, TraversedEdge, bool]] = []
    for node_id in frontier:
        for direction_label, relations in (
            ("out", outgoing.get(node_id, [])),
            ("in", incoming.get(node_id, [])),
        ):
            kept = 0
            for relation in sorted(relations, key=_edge_sort_key):
                far_id = (
                    relation.to_symbol_id if direction_label == "out" else relation.from_symbol_id
                )
                if far_id is None:
                    state.unresolved_edges += 1
                    if depth == 1 and len(state.null_target_references) < MAX_NULL_TARGET_REFS:
                        # Seed-level ambiguity is evidence the caller needs to see,
                        # not just a count.
                        state.null_target_references.append(relation)
                    continue
                if relation.id in state.seen_edge_ids:
                    continue
                if kept >= limits.max_edges_per_node:
                    state.suppressed[node_id] = state.suppressed.get(node_id, 0) + 1
                    continue
                if len(state.edges) >= limits.max_total_edges:
                    state.guards["max_total_edges"] = True
                    return discovered
                state.seen_edge_ids.add(relation.id)
                edge = TraversedEdge(relation=relation, depth=depth, direction=direction_label)
                state.edges.append(edge)
                state.depth_reached = depth
                kept += 1
                transit = is_transit_edge(relation)
                if not transit:
                    state.heuristic_leaf_edges += 1
                if far_id not in state.nodes:
                    discovered.append((far_id, edge, transit))
    return discovered


def traverse(
    reads: ReadProjections,
    seeds: list[Symbol],
    direction: Direction,
    limits: TraversalLimits,
) -> TraversalOutcome:
    """Traverse stored contains/references edges breadth-first from the seeds.

    Trust policy: only exact/scoped references and containment are transit edges;
    heuristic (unique-name or unclassified) references are recorded as leaf evidence
    with their far node but are never expanded, so unrelated symbols cannot be
    chained through a shared name. Deterministic for one index state: frontier order
    follows seed rank and discovery order, per-node edges are expanded
    best-evidence-first, cycles terminate through the visited set, and every guard
    that stopped expansion is reported. Stored resolution and confidence are carried
    verbatim; suppressed fan-out, suppressed heuristic expansion, and unresolved
    endpoints are counted, never silently dropped.
    """
    limits = limits.clamped()
    state = _TraversalState(
        nodes={},
        edges=[],
        seen_edge_ids=set(),
        suppressed={},
        guards={"max_depth": False, "max_nodes": False, "max_total_edges": False},
    )
    for seed in seeds:
        if seed.id not in state.nodes:
            state.nodes[seed.id] = TraversedNode(symbol=seed, depth=0, parent_edge_id=None)
    frontier = list(state.nodes)

    for depth in range(1, limits.max_depth + 1):
        if not frontier:
            break
        outgoing: dict[str, list[Relation]] = {}
        incoming: dict[str, list[Relation]] = {}
        if direction is not Direction.IN:
            outgoing = _group_by_endpoint(
                reads.relations_from_symbols(frontier, kinds=TRAVERSAL_KINDS), endpoint="from"
            )
        if direction is not Direction.OUT:
            incoming = _group_by_endpoint(
                reads.relations_to_symbols(frontier, kinds=TRAVERSAL_KINDS), endpoint="to"
            )
        discovered = _expand_level(state, frontier, outgoing, incoming, depth, limits)

        new_ids: list[str] = []
        parent_edges: dict[str, tuple[TraversedEdge, bool]] = {}
        for far_id, edge, transit in discovered:
            if far_id in state.nodes or far_id in parent_edges:
                continue
            parent_edges[far_id] = (edge, transit)
            new_ids.append(far_id)
        capacity = limits.max_nodes - len(state.nodes)
        if len(new_ids) > capacity:
            state.guards["max_nodes"] = True
            new_ids = new_ids[: max(0, capacity)]
        found = reads.get_symbols_by_ids(new_ids)
        next_frontier: list[str] = []
        for far_id in new_ids:
            symbol = found.get(far_id)
            if symbol is None:
                state.dangling_targets += 1
                continue
            edge, transit = parent_edges[far_id]
            state.nodes[far_id] = TraversedNode(
                symbol=symbol, depth=depth, parent_edge_id=edge.relation.id
            )
            if transit:
                next_frontier.append(far_id)
        frontier = next_frontier
        if state.guards["max_total_edges"] or state.guards["max_nodes"]:
            break
    else:
        if frontier:
            state.guards["max_depth"] = True

    return TraversalOutcome(
        nodes=state.nodes,
        edges=tuple(state.edges),
        suppressed=state.suppressed,
        guards=state.guards,
        unresolved_edges=state.unresolved_edges,
        dangling_targets=state.dangling_targets,
        depth_reached=state.depth_reached,
        frontier_remaining=len(frontier),
        heuristic_leaf_edges=state.heuristic_leaf_edges,
        null_target_references=tuple(state.null_target_references),
    )
