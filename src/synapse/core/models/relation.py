"""Relation model: a directed edge between two symbols."""

from dataclasses import dataclass

from synapse.core.models.enums import Confidence, RelationKind, ResolutionMethod


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
    # Reference-site location; None for structural relations and legacy rows.
    # start_byte_col is a 1-based byte offset within the line (tree-sitter column).
    start_line: int | None = None
    start_byte_col: int | None = None
    resolution: ResolutionMethod | None = None
    # Syntactic category of the usage site, drawn from the language's advertised
    # reference_usage_kinds; None for structural relations and legacy rows.
    usage_kind: str | None = None
    # Dotted name written at the usage site, e.g. `Overlock.Api.Servers.Server`.
    to_qualified_name: str | None = None
