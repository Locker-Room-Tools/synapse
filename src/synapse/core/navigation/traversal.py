"""Batched one-hop relation retrieval and the stored-edge trust policy."""

from dataclasses import dataclass

from synapse.core.index import ReadProjections
from synapse.core.models import Confidence, Relation, RelationKind, ResolutionMethod

TRAVERSAL_KINDS: tuple[RelationKind, ...] = (RelationKind.CONTAINS, RelationKind.REFERENCES)

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
    """Classify a stored edge for navigation policy: exact, scoped, or heuristic.

    Containment is a parser-proven structural fact and counts as exact. Reference
    edges keep their stored resolution: exact and scoped are trustworthy;
    everything weaker (unique-name and unclassified) is heuristic.
    """
    if relation.kind is RelationKind.CONTAINS:
        return "exact"
    if relation.resolution is ResolutionMethod.EXACT:
        return "exact"
    if relation.resolution is ResolutionMethod.SCOPED:
        return "scoped"
    return "heuristic"


def edge_sort_key(relation: Relation) -> tuple[int, int, int, int, str]:
    """Deterministic best-evidence-first ordering for stored relations."""
    return (
        _KIND_RANKS[relation.kind],
        resolution_rank(relation.resolution),
        _CONFIDENCE_RANKS[relation.confidence],
        relation.start_line or 0,
        relation.id,
    )


@dataclass(frozen=True, slots=True)
class OneHop:
    """Sorted incoming/outgoing relations for a set of selected symbols.

    ``incoming`` is keyed by ``to_symbol_id``; ``outgoing`` by ``from_symbol_id``.
    Every list is pre-sorted best-evidence-first; stored resolution and confidence
    are carried verbatim.
    """

    incoming: dict[str, list[Relation]]
    outgoing: dict[str, list[Relation]]


def one_hop(reads: ReadProjections, symbol_ids: list[str]) -> OneHop:
    """Retrieve one hop of stored contains/references edges around the symbols."""
    incoming: dict[str, list[Relation]] = {}
    for relation in reads.relations_to_symbols(symbol_ids, kinds=TRAVERSAL_KINDS):
        if relation.to_symbol_id is not None:
            incoming.setdefault(relation.to_symbol_id, []).append(relation)
    outgoing: dict[str, list[Relation]] = {}
    for relation in reads.relations_from_symbols(symbol_ids, kinds=TRAVERSAL_KINDS):
        if relation.from_symbol_id is not None:
            outgoing.setdefault(relation.from_symbol_id, []).append(relation)
    for grouped in (incoming, outgoing):
        for relations in grouped.values():
            relations.sort(key=edge_sort_key)
    return OneHop(incoming=incoming, outgoing=outgoing)
