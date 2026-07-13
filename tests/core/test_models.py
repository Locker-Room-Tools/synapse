"""Tests for the public Synapse core model API."""

from dataclasses import is_dataclass

from synapse.core import Confidence, Relation, RelationKind, SourceFile, Symbol, SymbolKind


def test_core_re_exports_model_types() -> None:
    """The core package re-exports the public model API."""
    assert is_dataclass(SourceFile)
    assert is_dataclass(Symbol)
    assert is_dataclass(Relation)
    assert str(SymbolKind.CLASS) == "class"
    assert str(RelationKind.REFERENCES) == "references"
    assert str(Confidence.HIGH) == "high"


def test_models_support_brief_aligned_fields() -> None:
    """The model types expose the brief-aligned field names and shapes."""
    source_file = SourceFile(
        id="file-1",
        path="src/synapse/core/models/symbol.py",
        language="python",
        project_root="/workspace",
        content_hash="abc123",
        indexed_at="2026-06-13T00:00:00Z",
    )
    symbol = Symbol(
        id="symbol-1",
        language="python",
        kind=SymbolKind.CLASS,
        native_kind="class_definition",
        name="SymbolIndex",
        qualified_name="synapse.core.index.SymbolIndex",
        file_path="src/synapse/core/index.py",
        container_id=None,
        start_line=1,
        end_line=10,
        start_byte=0,
        end_byte=128,
        signature="class SymbolIndex:",
        source="tree-sitter",
        confidence=Confidence.HIGH,
    )
    relation = Relation(
        id="relation-1",
        kind=RelationKind.CONTAINS,
        from_symbol_id="symbol-1",
        to_symbol_id="symbol-2",
        from_file_path=source_file.path,
        to_file_path="src/synapse/core/models/symbol.py",
        to_name="Symbol",
        source="tree-sitter",
        confidence=Confidence.MEDIUM,
    )

    assert symbol.qualified_name == "synapse.core.index.SymbolIndex"
    assert symbol.container_id is None
    assert relation.from_file_path == source_file.path
    assert relation.to_name == "Symbol"
