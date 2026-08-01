"""Hand-built index scenarios shared by navigation tests."""

from pathlib import Path

from synapse.core.index import SymbolIndex
from synapse.core.models import (
    Confidence,
    Relation,
    RelationKind,
    ResolutionMethod,
    SourceFile,
    Symbol,
    SymbolKind,
)


def make_symbol(
    symbol_id: str,
    name: str,
    file_path: str,
    *,
    kind: SymbolKind = SymbolKind.FUNCTION,
    line: int = 1,
    end_line: int | None = None,
    container_id: str | None = None,
    qualified_name: str | None = None,
    signature: str | None = None,
) -> Symbol:
    return Symbol(
        id=symbol_id,
        language="python",
        kind=kind,
        native_kind=str(kind),
        name=name,
        qualified_name=qualified_name if qualified_name is not None else name,
        file_path=file_path,
        container_id=container_id,
        start_line=line,
        end_line=end_line if end_line is not None else line + 1,
        start_byte=line * 100,
        end_byte=line * 100 + 50,
        signature=signature,
        source="tree-sitter",
        confidence=Confidence.HIGH,
    )


def make_reference(
    relation_id: str,
    *,
    from_symbol_id: str | None,
    to_symbol_id: str | None,
    from_file_path: str,
    to_file_path: str | None = None,
    to_name: str | None = None,
    resolution: ResolutionMethod | None = ResolutionMethod.EXACT,
    line: int = 1,
    # A real usage kind Python advertises as call-proven; `add_file` records every
    # file as Python, so this is what the extractor would actually have stored.
    usage_kind: str | None = "invocation",
    confidence: Confidence = Confidence.HIGH,
) -> Relation:
    return Relation(
        id=relation_id,
        kind=RelationKind.REFERENCES,
        from_symbol_id=from_symbol_id,
        to_symbol_id=to_symbol_id,
        from_file_path=from_file_path,
        to_file_path=to_file_path,
        to_name=to_name,
        source="tree-sitter",
        confidence=confidence,
        start_line=line,
        start_byte_col=1,
        resolution=resolution,
        usage_kind=usage_kind,
    )


def make_contains(container_id: str, child_id: str, file_path: str) -> Relation:
    return Relation(
        id=f"contains:{container_id}:{child_id}",
        kind=RelationKind.CONTAINS,
        from_symbol_id=container_id,
        to_symbol_id=child_id,
        from_file_path=file_path,
        to_file_path=file_path,
        to_name=None,
        source="tree-sitter",
        confidence=Confidence.HIGH,
    )


def build_index(tmp_path: Path) -> SymbolIndex:
    return SymbolIndex(tmp_path / "index.sqlite")


def add_file(
    index: SymbolIndex,
    file_path: str,
    symbols: list[Symbol],
    relations: list[Relation] | None = None,
    *,
    project_root: str = "/workspace",
) -> None:
    index.upsert_file(
        SourceFile(
            id=file_path,
            path=file_path,
            language="python",
            project_root=project_root,
            content_hash=f"hash-{file_path}",
            indexed_at="2026-01-01T00:00:00Z",
        )
    )
    index.replace_symbols_for_file(file_path, symbols, relations or [])
