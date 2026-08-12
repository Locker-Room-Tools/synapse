"""Tests for the SQLite symbol index."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

from synapse.core.index import SymbolIndex
from synapse.core.indexing.parser import build_relations, parse_file
from synapse.core.models import (
    Confidence,
    Relation,
    RelationKind,
    ResolutionMethod,
    SourceFile,
    Symbol,
    SymbolKind,
)


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
                "signature": "class Example:",
                "children": [
                    {
                        "symbol_id": method.id,
                        "kind": "method",
                        "name": "method",
                        "line_range": [2, 3],
                        "signature": "def method(self):",
                        "children": [],
                    }
                ],
            },
            {
                "symbol_id": next(symbol for symbol in symbols if symbol.name == "helper").id,
                "kind": "function",
                "name": "helper",
                "line_range": [5, 6],
                "signature": "def helper():",
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
    assert context["body_truncated"] is False


def test_get_symbol_context_caps_body_at_max_body_lines(tmp_path: Path) -> None:
    """include_body respects max_body_lines and reports the truncation."""
    index, symbols = _build_index(tmp_path)
    example = next(symbol for symbol in symbols if symbol.name == "Example")

    context = index.get_symbol_context(example.id, include_body=True, max_body_lines=1)
    assert context is not None
    assert context["body"] == "class Example:"
    assert context["body_truncated"] is True

    default_context = index.get_symbol_context(example.id)
    assert default_context is not None
    assert default_context["body"] is None
    assert default_context["body_truncated"] is False


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


def test_project_map_excludes_namespaces_and_aggregates_them(tmp_path: Path) -> None:
    """Namespaces never fill top_symbols slots; they are aggregated and deduplicated."""
    index = SymbolIndex(tmp_path / "index.sqlite")
    namespace_names = [f"Overlock.Feature{number:02d}" for number in range(25)]
    for file_number, file_id in enumerate(("a.cs", "b.cs")):
        index.upsert_file(
            SourceFile(
                id=file_id,
                path=file_id,
                language="csharp",
                project_root=str(tmp_path),
                content_hash=f"hash-{file_number}",
                indexed_at="2026-06-16T00:00:00+00:00",
            )
        )
        # The same 25 file-scoped namespace names recur in both files.
        symbols = [
            _test_symbol(
                symbol_id=f"ns-{file_number}-{number}",
                name=name,
                file_path=file_id,
                kind=SymbolKind.NAMESPACE,
                start_byte=number * 10,
                end_byte=number * 10 + 5,
            )
            for number, name in enumerate(namespace_names)
        ]
        index.replace_symbols_for_file(file_id, symbols, [])
    index.upsert_file(
        SourceFile(
            id="types.cs",
            path="types.cs",
            language="csharp",
            project_root=str(tmp_path),
            content_hash="hash-t",
            indexed_at="2026-06-16T00:00:00+00:00",
        )
    )
    type_symbols = [
        _test_symbol(
            symbol_id="cls-z",
            name="ZzzEndpoint",
            file_path="types.cs",
            kind=SymbolKind.CLASS,
            start_byte=0,
            end_byte=5,
        ),
        _test_symbol(
            symbol_id="rec-z",
            name="ZzzDto",
            file_path="types.cs",
            kind=SymbolKind.RECORD,
            start_byte=10,
            end_byte=15,
        ),
        _test_symbol(
            symbol_id="enum-a",
            name="AaaStatus",
            file_path="types.cs",
            kind=SymbolKind.ENUM,
            start_byte=20,
            end_byte=25,
        ),
        _test_symbol(
            symbol_id="type-a",
            name="AaaNotify",
            file_path="types.cs",
            kind=SymbolKind.TYPE,
            start_byte=30,
            end_byte=35,
        ),
    ]
    index.replace_symbols_for_file("types.cs", type_symbols, [])

    project_map = index.project_map(top_symbols_limit=20)
    top_symbols = cast(list[dict[str, object]], project_map["top_symbols"])

    # Free slots remain (4 declarations, limit 20) yet no namespace appears.
    assert len(top_symbols) == 4
    assert all(symbol["kind"] != "namespace" for symbol in top_symbols)
    # Kind ranking stays predictable: class, record, enum, then type (delegates).
    assert [symbol["name"] for symbol in top_symbols] == [
        "ZzzEndpoint",
        "ZzzDto",
        "AaaStatus",
        "AaaNotify",
    ]
    namespaces = cast(dict[str, object], project_map["namespaces"])
    items = cast(list[str], namespaces["items"])
    assert items == sorted(namespace_names)[:20]
    assert namespaces["total"] == 25
    assert namespaces["truncated"] is True
    # The namespace total counts distinct names, independently of the item limit.
    assert namespaces["total"] == len(set(namespace_names))
    assert index.project_map(top_symbols_limit=1)["namespaces"] == namespaces


def _diverse_kinds_index(tmp_path: Path) -> SymbolIndex:
    """One file holding many classes plus a few records and methods."""
    index = SymbolIndex(tmp_path / "index.sqlite")
    index.upsert_file(
        SourceFile(
            id="app.cs",
            path="app.cs",
            language="csharp",
            project_root=str(tmp_path),
            content_hash="hash-app",
            indexed_at="2026-06-16T00:00:00+00:00",
        )
    )
    symbols = [
        _test_symbol(
            symbol_id=f"cls-{number:02d}",
            name=f"Class{number:02d}",
            file_path="app.cs",
            kind=SymbolKind.CLASS,
            start_byte=number * 10,
            end_byte=number * 10 + 5,
        )
        for number in range(30)
    ]
    symbols += [
        _test_symbol(
            symbol_id=f"rec-{number:02d}",
            name=f"Record{number:02d}",
            file_path="app.cs",
            kind=SymbolKind.RECORD,
            start_byte=500 + number * 10,
            end_byte=500 + number * 10 + 5,
        )
        for number in range(4)
    ]
    symbols += [
        _test_symbol(
            symbol_id=f"mth-{number:02d}",
            name=f"Method{number:02d}",
            file_path="app.cs",
            kind=SymbolKind.METHOD,
            start_byte=900 + number * 10,
            end_byte=900 + number * 10 + 5,
        )
        for number in range(6)
    ]
    index.replace_symbols_for_file("app.cs", symbols, [])
    return index


def test_project_map_top_symbols_keep_kind_diversity(tmp_path: Path) -> None:
    """A class-heavy workspace still surfaces records and callable entry points."""
    index = _diverse_kinds_index(tmp_path)

    project_map = index.project_map(top_symbols_limit=12)
    top_symbols = cast(list[dict[str, object]], project_map["top_symbols"])
    kinds = {str(symbol["kind"]) for symbol in top_symbols}

    assert len(top_symbols) == 12
    # A strict kind cascade would return twelve classes and nothing else.
    assert kinds == {"class", "record", "method"}
    assert project_map["top_symbols_total"] == 40
    assert project_map["top_symbols_truncated"] is True
    # Ranking is deterministic: repeated calls agree exactly.
    assert top_symbols == cast(
        list[dict[str, object]], index.project_map(top_symbols_limit=12)["top_symbols"]
    )
    # Types still outrank callables within the page.
    kind_order = [str(symbol["kind"]) for symbol in top_symbols]
    assert kind_order.index("class") < kind_order.index("method")


def test_project_map_single_kind_workspace_still_fills_the_page(tmp_path: Path) -> None:
    """The per-kind cap never starves a page when only one kind exists."""
    index = SymbolIndex(tmp_path / "index.sqlite")
    index.upsert_file(
        SourceFile(
            id="only.cs",
            path="only.cs",
            language="csharp",
            project_root=str(tmp_path),
            content_hash="hash-only",
            indexed_at="2026-06-16T00:00:00+00:00",
        )
    )
    index.replace_symbols_for_file(
        "only.cs",
        [
            _test_symbol(
                symbol_id=f"cls-{number:02d}",
                name=f"Class{number:02d}",
                file_path="only.cs",
                kind=SymbolKind.CLASS,
                start_byte=number * 10,
                end_byte=number * 10 + 5,
            )
            for number in range(20)
        ],
        [],
    )

    top_symbols = cast(
        list[dict[str, object]], index.project_map(top_symbols_limit=10)["top_symbols"]
    )
    assert len(top_symbols) == 10
    assert {str(symbol["kind"]) for symbol in top_symbols} == {"class"}


def test_project_map_file_pages_do_not_change_global_aggregates(tmp_path: Path) -> None:
    """`tree`/`page` describe one page of files; symbol and namespace views are global."""
    index = SymbolIndex(tmp_path / "index.sqlite")
    for number in range(5):
        file_id = f"pkg/file_{number:02d}.cs"
        index.upsert_file(
            SourceFile(
                id=file_id,
                path=file_id,
                language="csharp",
                project_root=str(tmp_path),
                content_hash=f"hash-{number}",
                indexed_at="2026-06-16T00:00:00+00:00",
            )
        )
        index.replace_symbols_for_file(
            file_id,
            [
                _test_symbol(
                    symbol_id=f"cls-{number}",
                    name=f"Class{number}",
                    file_path=file_id,
                    kind=SymbolKind.CLASS,
                ),
                _test_symbol(
                    symbol_id=f"ns-{number}",
                    name="Shared.Namespace",
                    file_path=file_id,
                    kind=SymbolKind.NAMESPACE,
                    start_byte=100,
                    end_byte=105,
                ),
            ],
            [],
        )

    first = index.project_map(limit=2, offset=0)
    second = index.project_map(limit=2, offset=2)
    third = index.project_map(limit=2, offset=4)

    assert cast(dict[str, object], first["page"])["files"] == [
        "pkg/file_00.cs",
        "pkg/file_01.cs",
    ]
    assert cast(dict[str, object], second["page"])["files"] == [
        "pkg/file_02.cs",
        "pkg/file_03.cs",
    ]
    assert cast(dict[str, object], third["page"]) == {
        "limit": 2,
        "offset": 4,
        "returned": 1,
        "total": 5,
        "has_more": False,
        "files": ["pkg/file_04.cs"],
    }
    # Global aggregates repeat unchanged on every page.
    for page in (second, third):
        assert page["top_symbols"] == first["top_symbols"]
        assert page["namespaces"] == first["namespaces"]
        assert page["top_symbols_total"] == first["top_symbols_total"]
    # Five files declare the same namespace name; it is reported once.
    namespaces = cast(dict[str, object], first["namespaces"])
    assert namespaces["items"] == ["Shared.Namespace"]
    assert namespaces["total"] == 1


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
        "files": ["pkg/file_00.py", "pkg/file_01.py"],
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
    # `files` spans the whole result; the page-scoped view lives under page.files.
    assert references["files"] == [f"pkg/file_{number:02d}.py" for number in range(1, 55)]
    assert references["page"] == {
        "limit": 2,
        "offset": 1,
        "returned": 2,
        "total": 54,
        "has_more": True,
        "files": ["pkg/file_02.py", "pkg/file_03.py"],
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


def _reference_relation(
    *,
    relation_id: str,
    from_symbol_id: str,
    from_file_path: str,
    to_name: str,
    to_symbol_id: str | None = None,
    resolution: ResolutionMethod = ResolutionMethod.AMBIGUOUS,
    start_line: int = 1,
    start_byte_col: int = 1,
) -> Relation:
    return Relation(
        id=relation_id,
        kind=RelationKind.REFERENCES,
        from_symbol_id=from_symbol_id,
        to_symbol_id=to_symbol_id,
        from_file_path=from_file_path,
        to_file_path=None,
        to_name=to_name,
        source="tree-sitter",
        confidence=Confidence.MEDIUM if to_symbol_id else Confidence.LOW,
        start_line=start_line,
        start_byte_col=start_byte_col,
        resolution=ResolutionMethod.UNIQUE_NAME if to_symbol_id else resolution,
    )


def _servers_index(tmp_path: Path) -> tuple[SymbolIndex, list[Symbol]]:
    """Three same-name declarations plus ambiguous usage relations."""
    index = SymbolIndex(tmp_path / "index.sqlite")
    declarations = []
    for number, file_id in enumerate(("decl_a.cs", "decl_b.cs", "decl_c.cs")):
        index.upsert_file(
            SourceFile(
                id=file_id,
                path=file_id,
                language="csharp",
                project_root=str(tmp_path),
                content_hash=f"hash-{number}",
                indexed_at="2026-06-16T00:00:00+00:00",
            )
        )
        declaration = _test_symbol(
            symbol_id=f"servers-{number}",
            name="Servers",
            file_path=file_id,
            kind=SymbolKind.PROPERTY,
        )
        index.replace_symbols_for_file(file_id, [declaration], [])
        declarations.append(declaration)
    index.upsert_file(
        SourceFile(
            id="usage.cs",
            path="usage.cs",
            language="csharp",
            project_root=str(tmp_path),
            content_hash="hash-u",
            indexed_at="2026-06-16T00:00:00+00:00",
        )
    )
    caller = _test_symbol(symbol_id="caller", name="Caller", file_path="usage.cs")
    index.replace_symbols_for_file("usage.cs", [caller], [])
    index.add_relations_for_file(
        "usage.cs",
        [
            _reference_relation(
                relation_id="references:caller:Servers:10:100",
                from_symbol_id=caller.id,
                from_file_path="usage.cs",
                to_name="Servers",
                start_line=10,
                start_byte_col=21,
            ),
            _reference_relation(
                relation_id="references:caller:Servers:12:140",
                from_symbol_id=caller.id,
                from_file_path="usage.cs",
                to_name="Servers",
                start_line=12,
                start_byte_col=9,
            ),
        ],
    )
    return index, declarations


def test_find_references_marks_unique_name_matches_as_heuristic(tmp_path: Path) -> None:
    """Target-bound relations surface as heuristic items with exact locations."""
    index, symbols = _build_index(tmp_path)
    helper = next(symbol for symbol in symbols if symbol.name == "helper")
    method = next(symbol for symbol in symbols if symbol.name == "method")
    index.add_relations_for_file(
        "sample.py",
        [
            _reference_relation(
                relation_id="references:method:helper:3:60",
                from_symbol_id=method.id,
                from_file_path="sample.py",
                to_name="helper",
                to_symbol_id=helper.id,
                start_line=3,
                start_byte_col=16,
            )
        ],
    )

    result = index.find_references(symbol_id=helper.id)
    items = cast(list[dict[str, object]], result["items"])

    assert len(items) == 1
    assert items[0]["match"] == "heuristic"
    assert items[0]["line"] == 3
    assert items[0]["byte_column"] == 16
    assert items[0]["confidence"] == "medium"
    assert "candidate_symbol_ids" not in items[0]
    assert result["possible_items"] == []
    coverage = cast(dict[str, object], result["coverage"])
    assert coverage["resolution_model"] == "syntactic-structural"
    assert coverage["exhaustive"] is False
    assert coverage["counts"] == {
        "exact": 0,
        "scoped": 0,
        "heuristic": 1,
        "ambiguous": 0,
        "unresolved": 0,
        "resolved": 0,
    }
    assert "zero_result" not in coverage


def test_same_name_declarations_get_ambiguous_possible_items_not_confirmed_usages(
    tmp_path: Path,
) -> None:
    """Shared same-name usages stay in possible_items for every candidate target."""
    index, declarations = _servers_index(tmp_path)

    for declaration in declarations:
        result = index.find_references(symbol_id=declaration.id)
        # Nothing is presented as a confirmed usage of this specific target.
        assert result["items"] == []
        possible_items = cast(list[dict[str, object]], result["possible_items"])
        assert len(possible_items) == 2
        for item in possible_items:
            assert item["match"] == "ambiguous"
            assert item["to_symbol_id"] is None
            candidate_ids = cast(list[str], item["candidate_symbol_ids"])
            assert declaration.id in candidate_ids
            assert item["candidate_count"] == 3
            assert item["candidates_truncated"] is False
        coverage = cast(dict[str, object], result["coverage"])
        assert coverage["counts"] == {
            "exact": 0,
            "scoped": 0,
            "heuristic": 0,
            "ambiguous": 2,
            "unresolved": 0,
            "resolved": 0,
        }


def _paged_servers_index(
    tmp_path: Path,
    *,
    ambiguous: int,
    confirmed: int = 0,
) -> tuple[SymbolIndex, Symbol]:
    """Three same-name declarations plus N ambiguous and M confirmed usage relations.

    Every relation lives in its own file so the documented ordering
    (`from_file_path`, line, byte column, id) is fully determined by the file name.
    """
    index = SymbolIndex(tmp_path / "index.sqlite")
    declarations: list[Symbol] = []
    for number, file_id in enumerate(("decl_a.cs", "decl_b.cs", "decl_c.cs")):
        index.upsert_file(
            SourceFile(
                id=file_id,
                path=file_id,
                language="csharp",
                project_root=str(tmp_path),
                content_hash=f"hash-decl-{number}",
                indexed_at="2026-06-16T00:00:00+00:00",
            )
        )
        declaration = _test_symbol(
            symbol_id=f"servers-{number}",
            name="Servers",
            file_path=file_id,
            kind=SymbolKind.PROPERTY,
        )
        index.replace_symbols_for_file(file_id, [declaration], [])
        declarations.append(declaration)

    for number in range(ambiguous + confirmed):
        # Confirmed relations sort after the ambiguous ones by file name, which keeps
        # the two collections' page windows visibly independent.
        prefix = "amb" if number < ambiguous else "hit"
        file_id = f"{prefix}_{number:02d}.cs"
        index.upsert_file(
            SourceFile(
                id=file_id,
                path=file_id,
                language="csharp",
                project_root=str(tmp_path),
                content_hash=f"hash-{file_id}",
                indexed_at="2026-06-16T00:00:00+00:00",
            )
        )
        caller = _test_symbol(
            symbol_id=f"caller-{number:02d}",
            name=f"Caller{number:02d}",
            file_path=file_id,
        )
        index.replace_symbols_for_file(file_id, [caller], [])
        index.add_relations_for_file(
            file_id,
            [
                _reference_relation(
                    relation_id=f"references:{caller.id}:Servers:{number}:{number * 10}",
                    from_symbol_id=caller.id,
                    from_file_path=file_id,
                    to_name="Servers",
                    to_symbol_id=declarations[0].id if number >= ambiguous else None,
                    start_line=number + 1,
                    start_byte_col=1,
                )
            ],
        )
    return index, declarations[0]


def test_possible_items_are_paged_rather_than_restarted_on_every_page(
    tmp_path: Path,
) -> None:
    """Ambiguous results beyond the first page are reachable via offset."""
    index, target = _paged_servers_index(tmp_path, ambiguous=5)

    first = index.find_references(symbol_id=target.id, limit=2, offset=0)
    second = index.find_references(symbol_id=target.id, limit=2, offset=2)
    third = index.find_references(symbol_id=target.id, limit=2, offset=4)

    # No confirmed usages exist, so the confirmed page is empty on every request.
    assert first["items"] == []
    assert cast(dict[str, object], first["page"])["total"] == 0
    assert first["possible_total"] == 5

    assert first["possible_page"] == {
        "limit": 2,
        "offset": 0,
        "returned": 2,
        "total": 5,
        "has_more": True,
    }
    assert second["possible_page"] == {
        "limit": 2,
        "offset": 2,
        "returned": 2,
        "total": 5,
        "has_more": True,
    }
    assert third["possible_page"] == {
        "limit": 2,
        "offset": 4,
        "returned": 1,
        "total": 5,
        "has_more": False,
    }

    def paths(result: dict[str, object]) -> list[str]:
        items = cast(list[dict[str, object]], result["possible_items"])
        return [cast(str, item["from_file_path"]) for item in items]

    assert paths(first) == ["amb_00.cs", "amb_01.cs"]
    assert paths(second) == ["amb_02.cs", "amb_03.cs"]
    assert paths(third) == ["amb_04.cs"]


def test_mixed_confirmed_and_ambiguous_collections_page_independently(
    tmp_path: Path,
) -> None:
    """Both collections honour the same window while reporting separate page blocks."""
    index, target = _paged_servers_index(tmp_path, ambiguous=4, confirmed=3)

    result = index.find_references(symbol_id=target.id, limit=2, offset=2)

    items = cast(list[dict[str, object]], result["items"])
    possible_items = cast(list[dict[str, object]], result["possible_items"])
    assert [item["from_file_path"] for item in items] == ["hit_06.cs"]
    assert [item["from_file_path"] for item in possible_items] == ["amb_02.cs", "amb_03.cs"]

    assert result["page"] == {
        "limit": 2,
        "offset": 2,
        "returned": 1,
        "total": 3,
        "has_more": False,
        "files": ["amb_02.cs", "amb_03.cs", "hit_06.cs"],
    }
    assert result["possible_page"] == {
        "limit": 2,
        "offset": 2,
        "returned": 2,
        "total": 4,
        "has_more": False,
    }
    # Global aggregates stay comparable across pages.
    assert result["files"] == [
        "amb_00.cs",
        "amb_01.cs",
        "amb_02.cs",
        "amb_03.cs",
        "hit_04.cs",
        "hit_05.cs",
        "hit_06.cs",
    ]
    coverage = cast(dict[str, object], result["coverage"])
    assert coverage["counts"] == {
        "exact": 0,
        "scoped": 0,
        "heuristic": 3,
        "ambiguous": 4,
        "unresolved": 0,
        "resolved": 0,
    }


def test_consecutive_reference_pages_have_no_duplicates_or_omissions(
    tmp_path: Path,
) -> None:
    """Walking every page yields each relation exactly once in both collections."""
    index, target = _paged_servers_index(tmp_path, ambiguous=7, confirmed=5)

    seen_confirmed: list[str] = []
    seen_possible: list[str] = []
    offset = 0
    while True:
        result = index.find_references(symbol_id=target.id, limit=3, offset=offset)
        items = cast(list[dict[str, object]], result["items"])
        possible_items = cast(list[dict[str, object]], result["possible_items"])
        seen_confirmed.extend(cast(str, item["from_file_path"]) for item in items)
        seen_possible.extend(cast(str, item["from_file_path"]) for item in possible_items)
        page = cast(dict[str, object], result["page"])
        possible_page = cast(dict[str, object], result["possible_page"])
        if not page["has_more"] and not possible_page["has_more"]:
            break
        offset += 3

    assert seen_confirmed == [f"hit_{number:02d}.cs" for number in range(7, 12)]
    assert seen_possible == [f"amb_{number:02d}.cs" for number in range(7)]
    assert len(seen_confirmed) == len(set(seen_confirmed))
    assert len(seen_possible) == len(set(seen_possible))


def test_find_references_ambiguous_items_expose_candidates(tmp_path: Path) -> None:
    """Membership uses the full candidate set; only serialized ids are capped."""
    index = SymbolIndex(tmp_path / "index.sqlite")
    widget_ids = []
    for number in range(10):
        file_id = f"widget_{number:02d}.cs"
        index.upsert_file(
            SourceFile(
                id=file_id,
                path=file_id,
                language="csharp",
                project_root=str(tmp_path),
                content_hash=f"hash-{number}",
                indexed_at="2026-06-16T00:00:00+00:00",
            )
        )
        declaration = _test_symbol(
            symbol_id=f"widget-{number:02d}",
            name="Widget",
            file_path=file_id,
            kind=SymbolKind.CLASS,
        )
        index.replace_symbols_for_file(file_id, [declaration], [])
        widget_ids.append(declaration.id)
    index.upsert_file(
        SourceFile(
            id="usage.cs",
            path="usage.cs",
            language="csharp",
            project_root=str(tmp_path),
            content_hash="hash-u",
            indexed_at="2026-06-16T00:00:00+00:00",
        )
    )
    caller = _test_symbol(symbol_id="caller", name="Caller", file_path="usage.cs")
    index.replace_symbols_for_file("usage.cs", [caller], [])
    index.add_relations_for_file(
        "usage.cs",
        [
            _reference_relation(
                relation_id="references:caller:Widget:4:80",
                from_symbol_id=caller.id,
                from_file_path="usage.cs",
                to_name="Widget",
                start_line=4,
                start_byte_col=13,
            )
        ],
    )

    # The queried target is one that a capped candidate list could omit.
    target_id = widget_ids[-1]
    result = index.find_references(symbol_id=target_id)
    possible_items = cast(list[dict[str, object]], result["possible_items"])

    assert result["items"] == []
    assert len(possible_items) == 1
    item = possible_items[0]
    assert item["match"] == "ambiguous"
    assert item["candidate_count"] == 10
    assert item["candidates_truncated"] is True
    assert len(cast(list[str], item["candidate_symbol_ids"])) == 8


def test_find_references_zero_results_carry_partial_coverage(tmp_path: Path) -> None:
    """An empty answer states partial coverage instead of implying proven absence."""
    index, symbols = _build_index(tmp_path)
    example = next(symbol for symbol in symbols if symbol.name == "Example")

    result = index.find_references(symbol_id=example.id)

    assert result["items"] == []
    assert result["possible_items"] == []
    assert result["possible_total"] == 0
    coverage = cast(dict[str, object], result["coverage"])
    assert coverage["zero_result"] == "no-indexed-matches"
    assert coverage["exhaustive"] is False
    extraction = cast(list[dict[str, object]], coverage["extraction"])
    assert extraction and extraction[0]["language"] == "python"
    assert extraction[0]["completeness"] == "partial"


def test_relations_table_migrates_v2_columns(tmp_path: Path) -> None:
    """Reopening a v2 database adds location columns; legacy rows read as None."""
    database_path = tmp_path / "index.sqlite"
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute(
            """
            CREATE TABLE relations (
                id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                from_symbol_id TEXT,
                to_symbol_id TEXT,
                from_file_path TEXT NOT NULL,
                to_file_path TEXT,
                to_name TEXT,
                source TEXT NOT NULL,
                confidence TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO relations (
                id, file_id, kind, from_symbol_id, to_symbol_id, from_file_path,
                to_file_path, to_name, source, confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "references:legacy:Ghost:1:0",
                "legacy.cs",
                "references",
                "legacy-symbol",
                None,
                "legacy.cs",
                None,
                "Ghost",
                "tree-sitter",
                "low",
            ),
        )
        connection.execute("PRAGMA user_version = 2")

    reopened = SymbolIndex(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(relations)")}
    assert {"start_line", "start_byte_col", "resolution"} <= columns
    legacy = reopened.get_references_by_name("Ghost")
    assert len(legacy) == 1
    assert legacy[0].start_line is None
    assert legacy[0].start_byte_col is None
    assert legacy[0].resolution is None
