"""Tests for the SQLite symbol index."""

from pathlib import Path
from typing import cast

from synapse.core.index import SymbolIndex
from synapse.core.models import Confidence, Relation, RelationKind, SourceFile, Symbol
from synapse.core.parser import build_relations, parse_file


def _build_index(tmp_path: Path) -> tuple[SymbolIndex, list[Symbol]]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    file_path = workspace_root / "sample.py"
    file_path.write_text(
        "class Example:\n    def method(self):\n        return 1\n\ndef helper():\n    return 2\n",
        encoding="utf-8",
    )
    symbols = parse_file(file_path, "python", workspace_root=workspace_root)
    index = SymbolIndex(tmp_path / "index.sqlite")
    index.upsert_file(
        SourceFile(
            id="sample.py",
            path="sample.py",
            language="python",
            project_root=str(workspace_root),
            content_hash="hash-1",
            indexed_at="2026-06-16T00:00:00+00:00",
        )
    )
    index.replace_symbols_for_file("sample.py", symbols, build_relations(symbols))
    return index, symbols


def test_index_supports_search_definition_outline_and_context(tmp_path: Path) -> None:
    """The index stores symbols and serves the Phase 1 lookup surface."""
    index, symbols = _build_index(tmp_path)
    method = next(symbol for symbol in symbols if symbol.name == "method")

    assert [item.name for item in index.search_symbols("helper")] == ["helper"]
    assert [item.name for item in index.get_definition("Example")] == ["Example"]
    outline = index.get_file_outline("sample.py")
    assert outline == {
        "file_path": "sample.py",
        "language": "python",
        "symbols": [
            {
                "symbol_id": next(symbol for symbol in symbols if symbol.name == "Example").id,
                "kind": "class",
                "name": "Example",
                "line_range": [1, 3],
                "children": [
                    {
                        "symbol_id": method.id,
                        "kind": "method",
                        "name": "method",
                        "line_range": [2, 3],
                        "children": [],
                    }
                ],
            },
            {
                "symbol_id": next(symbol for symbol in symbols if symbol.name == "helper").id,
                "kind": "function",
                "name": "helper",
                "line_range": [5, 6],
                "children": [],
            },
        ],
    }
    context = index.get_symbol_context(method.id, include_body=True)
    assert context is not None
    symbol_payload = cast(dict[str, object], context["symbol"])
    parent_payload = cast(dict[str, object], context["parent"])
    body = cast(str, context["body"])
    assert symbol_payload["name"] == "method"
    assert parent_payload["name"] == "Example"
    assert "def method" in body


def test_get_dependencies_returns_outgoing_relations(tmp_path: Path) -> None:
    """A container symbol exposes a CONTAINS dependency to its members."""
    index, symbols = _build_index(tmp_path)
    example = next(symbol for symbol in symbols if symbol.name == "Example")
    method = next(symbol for symbol in symbols if symbol.name == "method")

    dependencies = index.get_dependencies(example.id)

    assert any(
        relation.kind == "contains" and relation.to_symbol_id == method.id
        for relation in dependencies
    )


def test_remove_files_deletes_tracked_file_rows(tmp_path: Path) -> None:
    """Removing a file removes it from the tracked file list."""
    index, _ = _build_index(tmp_path)

    assert index.remove_files(["sample.py"]) == 1
    assert index.list_indexed_files() == []


def test_workspace_stats_project_map_and_file_dependencies(tmp_path: Path) -> None:
    """The index exposes workspace-level summaries and file import dependencies."""
    index, symbols = _build_index(tmp_path)

    stats = index.workspace_stats()
    assert stats["files"] == 1
    assert stats["symbols"] == len(symbols)
    assert stats["languages"] == [{"language": "python", "files": 1, "percent": 100.0}]

    project_map = index.project_map()
    assert project_map["tree"] == {"sample.py": None}
    top_symbols = cast(list[dict[str, object]], project_map["top_symbols"])
    assert {symbol["name"] for symbol in top_symbols} >= {"Example", "helper", "method"}

    workspace_root = tmp_path / "imports-workspace"
    workspace_root.mkdir()
    file_path = workspace_root / "imports.py"
    file_path.write_text("import os\nfrom pkg import thing\n", encoding="utf-8")
    import_symbols = parse_file(file_path, "python", workspace_root=workspace_root)
    index.upsert_file(
        SourceFile(
            id="imports.py",
            path="imports.py",
            language="python",
            project_root=str(workspace_root),
            content_hash="hash-2",
            indexed_at="2026-06-16T00:00:00+00:00",
        )
    )
    index.replace_symbols_for_file("imports.py", import_symbols, build_relations(import_symbols))

    assert index.get_file_dependencies("imports.py") == {
        "file_path": "imports.py",
        "imports": ["os", "pkg"],
    }
    assert index.get_file_dependencies("missing.py") is None


def test_reference_lookup_related_symbols_and_compact_context(tmp_path: Path) -> None:
    """Reference relations power reverse lookups and compact context composition."""
    index, symbols = _build_index(tmp_path)
    by_name = {symbol.name: symbol for symbol in symbols}
    method = by_name["method"]
    helper = by_name["helper"]
    references = [
        Relation(
            id="references:method:helper:1",
            kind=RelationKind.REFERENCES,
            from_symbol_id=method.id,
            to_symbol_id=helper.id,
            from_file_path="sample.py",
            to_file_path="sample.py",
            to_name="helper",
            source="tree-sitter",
            confidence=Confidence.HIGH,
        ),
        Relation(
            id="references:method:missing:1",
            kind=RelationKind.REFERENCES,
            from_symbol_id=method.id,
            to_symbol_id=None,
            from_file_path="sample.py",
            to_file_path=None,
            to_name="missing",
            source="tree-sitter",
            confidence=Confidence.LOW,
        ),
    ]

    index.add_relations_for_file("sample.py", references)

    assert index.symbol_name_index()["helper"] == [helper.id]
    assert [relation.to_symbol_id for relation in index.get_references(helper.id)] == [helper.id]
    assert [relation.to_name for relation in index.get_references_by_name("missing")] == ["missing"]
    assert index.find_references(name="helper")["files"] == ["sample.py"]
    related = index.related_symbols(method.id)
    assert related is not None
    related_items = cast(list[dict[str, object]], related["related"])
    assert related_items[0]["name"] == "helper"
    compact = index.compact_context(method.id)
    assert compact is not None
    depends_on = cast(list[str], compact["depends_on"])
    related_names = cast(list[str], compact["related"])
    assert "helper" in depends_on
    assert "helper" in related_names

    index.add_relations_for_file("sample.py", references[:1])
    assert index.get_references_by_name("missing") == []


def test_definition_result_is_reference_ready(tmp_path: Path) -> None:
    """get_definition by name returns a symbol_id suitable for find_references."""
    index, symbols = _build_index(tmp_path)
    helper = next(symbol for symbol in symbols if symbol.name == "helper")

    result = index.get_definition("helper")
    assert len(result) == 1
    assert result[0].id == helper.id

    refs = index.find_references(symbol_id=helper.id)
    assert isinstance(refs, dict)
    assert "items" in refs
