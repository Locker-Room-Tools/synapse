"""Symbol model: a normalized code symbol discovered during indexing."""

from dataclasses import dataclass

from synapse.core.models.enums import Confidence, SymbolKind


@dataclass(frozen=True, slots=True)
class Symbol:
    """A normalized code symbol discovered during indexing."""

    id: str
    language: str
    kind: SymbolKind
    native_kind: str
    name: str
    qualified_name: str | None
    file_path: str
    container_id: str | None
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    signature: str | None
    source: str
    confidence: Confidence
