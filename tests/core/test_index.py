"""Tests for the SQLite symbol index."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

from synapse.core.index import SymbolIndex
from synapse.core.models import (
    Confidence,
    Relation,
    RelationKind,
    SourceFile,
    Symbol,
    SymbolKind,
)
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


def _test_symbol(
    *,
    symbol_id: str,
    name: str,
    file_path: str,
    kind: SymbolKind = SymbolKind.FUNCTION,
    container_id: str | None = None,
    start_byte: int = 0,
    end_byte: int = 10,
) -> Symbol:
    return Symbol(
        id=symbol_id,
        language="python",
        kind=kind,
        native_kind="test",
        name=name,
        qualified_name=name,
        file_path=file_path,
        container_id=container_id,
        start_line=start_byte + 1,
        end_line=end_byte + 1,
        start_byte=start_byte,
        end_byte=end_byte,
        signature=None,
        source="test",
        confidence=Confidence.HIGH,
    )


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
        "returned": 3,
        "total": 3,
        "truncated": False,
    }
    context = index.get_symbol_context(method.id, include_body=True)
    assert context is not None
    symbol_payload = cast(dict[str, object], context["symbol"])
    parent_payload = cast(dict[str, object], context["parent"])
    body = cast(str, context["body"])
    assert symbol_payload["name"] == "method"
    assert parent_payload["name"] == "Example"
    assert "def method" in body


def test_search_symbols_matches_prefix_substring_and_filters(tmp_path: Path) -> None:
    """FTS prefix search, substring fallback, and kind/language filters agree."""
    index, _ = _build_index(tmp_path)

    assert [item.name for item in index.search_symbols("help")] == ["helper"]
    assert [item.name for item in index.search_symbols("elpe")] == ["helper"]
    assert [item.name for item in index.search_symbols("helper", kind="function")] == ["helper"]
    assert index.search_symbols("helper", kind="class") == []
    assert index.search_symbols("helper", language="go") == []
    assert index.search_symbols("no_such_symbol") == []
    assert index.search_symbols('quote"quote') == []

    exact_first = index.search_symbols("Example")
    assert exact_first and exact_first[0].name == "Example"


def test_search_symbols_stays_in_sync_after_replace_and_remove(tmp_path: Path) -> None:
    """The FTS table tracks symbol rewrites and cascade deletes."""
    index, symbols = _build_index(tmp_path)

    index.replace_symbols_for_file("sample.py", [], [])
    assert index.search_symbols("helper") == []

    index.replace_symbols_for_file("sample.py", symbols, [])
    assert [item.name for item in index.search_symbols("helper")] == ["helper"]

    assert index.remove_files(["sample.py"]) == 1
    assert index.search_symbols("helper") == []


def test_search_symbols_survives_schema_migration(tmp_path: Path) -> None:
    """Reopening a database created before the FTS table rebuilds the search index."""
    index, _ = _build_index(tmp_path)
    with closing(sqlite3.connect(tmp_path / "index.sqlite")) as connection, connection:
        connection.execute("DROP TRIGGER symbols_fts_after_insert")
        connection.execute("DROP TRIGGER symbols_fts_after_delete")
        connection.execute("DROP TRIGGER symbols_fts_after_update")
        connection.execute("DROP TABLE symbols_fts")
        connection.execute("PRAGMA user_version = 0")

    reopened = SymbolIndex(tmp_path / "index.sqlite")
    assert [item.name for item in reopened.search_symbols("helper")] == ["helper"]


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
        "page": {
            "limit": 50,
            "offset": 0,
            "returned": 2,
            "total": 2,
            "has_more": False,
        },
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


def test_collection_queries_are_bounded_paged_and_stably_ordered(tmp_path: Path) -> None:
    """Every high-cardinality projection returns deterministic bounded pages."""
    index = SymbolIndex(tmp_path / "paged.sqlite")
    duplicate_symbols: list[Symbol] = []
    with index.transaction() as connection:
        for number in range(55):
            file_path = f"pkg/file_{number:02}.py"
            symbol = _test_symbol(
                symbol_id=f"duplicate-{number:02}",
                name="duplicate",
                file_path=file_path,
            )
            duplicate_symbols.append(symbol)
            index.upsert_file(
                SourceFile(
                    id=file_path,
                    path=file_path,
                    language="python",
                    project_root=str(tmp_path),
                    content_hash=str(number),
                    indexed_at="2026-07-17T00:00:00+00:00",
                ),
                connection=connection,
            )
            index.replace_symbols_for_file(file_path, [symbol], connection=connection)

        target = duplicate_symbols[0]
        for number, symbol in enumerate(duplicate_symbols[1:], start=1):
            index.add_relations_for_file(
                symbol.file_path,
                [
                    Relation(
                        id=f"reference-{number:02}",
                        kind=RelationKind.REFERENCES,
                        from_symbol_id=symbol.id,
                        to_symbol_id=target.id,
                        from_file_path=symbol.file_path,
                        to_file_path=target.file_path,
                        to_name=target.name,
                        source="test",
                        confidence=Confidence.HIGH,
                    )
                ],
                connection=connection,
            )

        outline_path = "outline.py"
        parent = _test_symbol(
            symbol_id="parent",
            name="Parent",
            file_path=outline_path,
            kind=SymbolKind.CLASS,
            end_byte=100,
        )
        children = [
            _test_symbol(
                symbol_id=f"child-{number}",
                name=f"child_{number}",
                file_path=outline_path,
                kind=SymbolKind.METHOD,
                container_id=parent.id,
                start_byte=10 + number * 10,
                end_byte=15 + number * 10,
            )
            for number in range(4)
        ]
        imports = [
            _test_symbol(
                symbol_id=f"import-{number}",
                name=f"pkg{number}",
                file_path=outline_path,
                kind=SymbolKind.IMPORT,
                start_byte=60 + number,
                end_byte=61 + number,
            )
            for number in range(3)
        ]
        outline_symbols = [parent, *children, *imports]
        index.upsert_file(
            SourceFile(
                id=outline_path,
                path=outline_path,
                language="python",
                project_root=str(tmp_path),
                content_hash="outline",
                indexed_at="2026-07-17T00:00:00+00:00",
            ),
            connection=connection,
        )
        index.replace_symbols_for_file(
            outline_path,
            outline_symbols,
            build_relations(outline_symbols),
            connection=connection,
        )

    search_items, search_page = index.search_symbols_page(
        "duplicate",
        limit=2,
        offset=2,
    )
    assert [symbol.file_path for symbol in search_items] == [
        "pkg/file_02.py",
        "pkg/file_03.py",
    ]
    assert search_page == {
        "limit": 2,
        "offset": 2,
        "returned": 2,
        "total": 55,
        "has_more": True,
    }
    _, clamped_page = index.search_symbols_page("duplicate", limit=999, offset=-10)
    assert clamped_page == {
        "limit": 200,
        "offset": 0,
        "returned": 55,
        "total": 55,
        "has_more": False,
    }

    definition_items, definition_page = index.get_definition_page(
        "duplicate",
        limit=2,
        offset=1,
    )
    assert [symbol.file_path for symbol in definition_items] == [
        "pkg/file_01.py",
        "pkg/file_02.py",
    ]
    assert definition_page["total"] == 55
    assert definition_page["has_more"] is True

    project_map = index.project_map(limit=2, offset=1, top_symbols_limit=999)
    assert project_map["tree"] == {"pkg": {"file_00.py": None, "file_01.py": None}}
    assert project_map["page"] == {
        "limit": 2,
        "offset": 1,
        "returned": 2,
        "total": 56,
        "has_more": True,
    }
    assert len(cast(list[dict[str, object]], project_map["top_symbols"])) == 50

    outline = index.get_file_outline("outline.py", max_symbols=3)
    assert outline is not None
    assert (outline["returned"], outline["total"], outline["truncated"]) == (3, 8, True)

    context = index.get_symbol_context(
        "parent",
        children_limit=2,
        children_offset=1,
    )
    assert context is not None
    context_children = cast(list[dict[str, object]], context["children"])
    assert [child["name"] for child in context_children] == ["child_1", "child_2"]
    assert context["page"] == {
        "limit": 2,
        "offset": 1,
        "returned": 2,
        "total": 4,
        "has_more": True,
    }

    dependencies, dependency_page = index.get_dependencies_page(
        "parent",
        limit=2,
        offset=1,
    )
    assert [relation.to_name for relation in dependencies] == ["child_1", "child_2"]
    assert dependency_page["total"] == 4

    file_dependencies = index.get_file_dependencies("outline.py", limit=1, offset=1)
    assert file_dependencies is not None
    assert file_dependencies["imports"] == ["pkg1"]
    assert file_dependencies["page"] == {
        "limit": 1,
        "offset": 1,
        "returned": 1,
        "total": 3,
        "has_more": True,
    }

    references = index.find_references(symbol_id="duplicate-00", limit=2, offset=1)
    assert references["files"] == ["pkg/file_02.py", "pkg/file_03.py"]
    assert references["page"] == {
        "limit": 2,
        "offset": 1,
        "returned": 2,
        "total": 54,
        "has_more": True,
    }

    related = index.related_symbols("duplicate-00", limit=2, offset=1)
    assert related is not None
    related_items = cast(list[dict[str, object]], related["related"])
    assert [item["file_path"] for item in related_items] == [
        "pkg/file_02.py",
        "pkg/file_03.py",
    ]
    assert related["page"] == {
        "limit": 2,
        "offset": 1,
        "returned": 2,
        "total": 54,
        "has_more": True,
    }
