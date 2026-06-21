"""Relation model: a directed edge between two symbols."""

from dataclasses import dataclass

from synapse.core.models.enums import Confidence, RelationKind


@dataclass(frozen=True, slots=True)
class Relation:
    """A directed edge between two symbols (or file-level references)."""

    id: str
    kind: RelationKind
    from_symbol_id: str | None
    to_symbol_id: str | None
    from_file_path: str
    to_file_path: str | None
    to_name: str | None
    source: str
    confidence: Confidence