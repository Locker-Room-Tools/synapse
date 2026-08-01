"""Compact symbol handles: derivation, storage, migration, and lookup."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from synapse.core.index import SymbolIndex, is_symbol_handle, read_symbol_source, symbol_handle
from synapse.core.index import writes as writes_module
from synapse.core.models import Confidence, SourceFile, Symbol, SymbolKind


def _symbol(symbol_id: str, name: str, file_path: str, *, line: int = 1) -> Symbol:
    return Symbol(
        id=symbol_id,
        language="python",
        kind=SymbolKind.FUNCTION,
        native_kind="function_definition",
        name=name,
        qualified_name=name,
        file_path=file_path,
        container_id=None,
        start_line=line,
        end_line=line + 1,
        start_byte=0,
        end_byte=10,
        signature=f"def {name}():",
        source="tree-sitter",
        confidence=Confidence.HIGH,
    )


def _add_file(index: SymbolIndex, file_path: str, symbols: list[Symbol]) -> None:
    index.upsert_file(
        SourceFile(
            id=file_path,
            path=file_path,
            language="python",
            project_root="/workspace",
            content_hash="hash",
            indexed_at="2026-01-01T00:00:00Z",
        )
    )
    index.replace_symbols_for_file(file_path, symbols)


def test_symbol_handle_is_stable_and_well_formed() -> None:
    """The digest is a fixed function of the stable ID: same input, same 24-char handle."""
    handle = symbol_handle("python:app/service.py:function:build_service:10")
    assert handle == "s_z0hMwyxH6530JqO8ymmmaw"
    assert is_symbol_handle(handle)
    assert not is_symbol_handle("s_short")
    assert not is_symbol_handle("python:app/service.py:function:build_service:10")
    assert not is_symbol_handle("x_z0hMwyxH6530JqO8ymmmaw")


def test_inserted_symbols_carry_their_handle(tmp_path: Path) -> None:
    """The write path stores the derived handle; lookup by handle round-trips."""
    index = SymbolIndex(tmp_path / "index.sqlite")
    symbol = _symbol("py:one", "alpha", "app/one.py")
    _add_file(index, "app/one.py", [symbol])

    with index.read_session() as reads:
        found = reads.get_symbols_by_handles([symbol_handle("py:one"), "s_" + "A" * 22])
    assert set(found) == {symbol_handle("py:one")}
    assert found[symbol_handle("py:one")].id == "py:one"


def test_symbols_table_migrates_v4_handle_column(tmp_path: Path) -> None:
    """Reopening a v4 database adds and backfills the handle column under a unique index."""
    database_path = tmp_path / "index.sqlite"
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute(
            """
            CREATE TABLE symbols (
                id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                language TEXT NOT NULL,
                kind TEXT NOT NULL,
                native_kind TEXT NOT NULL,
                name TEXT NOT NULL,
                qualified_name TEXT,
                file_path TEXT NOT NULL,
                container_id TEXT,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                start_byte INTEGER NOT NULL,
                end_byte INTEGER NOT NULL,
                signature TEXT,
                source TEXT NOT NULL,
                confidence TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO symbols (
                id, file_id, language, kind, native_kind, name, qualified_name, file_path,
                container_id, start_line, end_line, start_byte, end_byte, signature,
                source, confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "py:legacy",
                "legacy.py",
                "python",
                "function",
                "function_definition",
                "legacy",
                "legacy",
                "legacy.py",
                None,
                1,
                2,
                0,
                10,
                None,
                "tree-sitter",
                "high",
            ),
        )
        connection.execute("PRAGMA user_version = 4")

    reopened = SymbolIndex(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(symbols)")}
        indexes = {
            str(row[1]) for row in connection.execute("PRAGMA index_list(symbols)").fetchall()
        }
        stored = connection.execute("SELECT handle FROM symbols WHERE id = 'py:legacy'").fetchone()[
            0
        ]
    assert "handle" in columns
    assert "idx_symbols_handle" in indexes
    assert stored == symbol_handle("py:legacy")
    with reopened.read_session() as reads:
        found = reads.get_symbols_by_handles([symbol_handle("py:legacy")])
    assert found[symbol_handle("py:legacy")].name == "legacy"


def test_handle_backfill_is_idempotent(tmp_path: Path) -> None:
    """Opening the index repeatedly never rewrites or duplicates handles."""
    database_path = tmp_path / "index.sqlite"
    index = SymbolIndex(database_path)
    _add_file(index, "app/one.py", [_symbol("py:one", "alpha", "app/one.py")])

    SymbolIndex(database_path)
    SymbolIndex(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute("SELECT id, handle FROM symbols").fetchall()
    assert rows == [("py:one", symbol_handle("py:one"))]


def test_handle_collision_fails_indexing_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A digest collision violates the unique index and aborts the write."""
    index = SymbolIndex(tmp_path / "index.sqlite")
    monkeypatch.setattr(writes_module, "symbol_handle", lambda symbol_id: "s_" + "C" * 22)

    with pytest.raises(sqlite3.IntegrityError):
        _add_file(
            index,
            "app/one.py",
            [_symbol("py:one", "alpha", "app/one.py"), _symbol("py:two", "beta", "app/one.py")],
        )


def test_files_matching_path_orders_by_match_strength(tmp_path: Path) -> None:
    """Exact path beats path suffix beats substring; ties break on path."""
    index = SymbolIndex(tmp_path / "index.sqlite")
    for path in ("service.py", "app/service.py", "app/sub/service.py", "app/service_util.py"):
        _add_file(index, path, [_symbol(f"py:{path}", "decl", path)])

    with index.read_session() as reads:
        # Path-shaped term (it carries an extension): substring matching applies.
        exact_first, exact_page = reads.files_matching_path("service.py")
        substring, substring_page = reads.files_matching_path("service_util.py")
        bounded, bounded_page = reads.files_matching_path("service.py", limit=2)
        escaped, escaped_page = reads.files_matching_path("%")
    assert exact_first == ["service.py", "app/service.py", "app/sub/service.py"]
    assert exact_page["total"] == 3
    assert substring == ["app/service_util.py"]
    assert substring_page["total"] == 1
    # The bound withholds rows but never hides how many there were.
    assert len(bounded) == 2
    assert bounded_page["total"] == 3
    assert escaped == []
    assert escaped_page["total"] == 0


def test_files_matching_path_is_term_shape_aware(tmp_path: Path) -> None:
    """A bare word matches whole path components only; a path-shaped term may substring.

    Retrieval, the page total, and orientation's acceptance all read this one rule, so a
    response can no longer call a term unmatched while claiming matching files were
    omitted.
    """
    index = SymbolIndex(tmp_path / "index.sqlite")
    for path in ("service.py", "app/service.py", "app/service_util.py", "app/handler_noise.py"):
        _add_file(index, path, [_symbol(f"py:{path}", "decl", path)])

    with index.read_session() as reads:
        # Bare word that appears only mid-filename: not a path component, so no match.
        bare_substring, bare_page = reads.files_matching_path("handler")
        # Bare word that is a whole trailing component: matched.
        bare_component, component_page = reads.files_matching_path("service.py")
        # Same stem, but path-shaped (it carries a separator), so substring applies and
        # the mid-filename match the bare form rejected is now reachable.
        path_like, path_like_page = reads.files_matching_path("app/service_")

    assert bare_substring == []
    assert bare_page["total"] == 0
    assert bare_component == ["service.py", "app/service.py"]
    assert component_page["total"] == 2
    assert path_like == ["app/service_util.py"]
    assert path_like_page["total"] == 1


def test_read_symbol_source_bounds_and_failures(tmp_path: Path) -> None:
    """Slices are bounded with a truncation flag; missing or stale files return None."""
    source_path = tmp_path / "app" / "one.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("\n".join(f"line {i}" for i in range(1, 11)), encoding="utf-8")
    wide = Symbol(
        id="py:wide",
        language="python",
        kind=SymbolKind.FUNCTION,
        native_kind="function_definition",
        name="wide",
        qualified_name="wide",
        file_path="app/one.py",
        container_id=None,
        start_line=2,
        end_line=9,
        start_byte=0,
        end_byte=10,
        signature=None,
        source="tree-sitter",
        confidence=Confidence.HIGH,
    )

    bounded = read_symbol_source(tmp_path, wide, max_lines=3)
    assert bounded is not None
    assert bounded.text == "line 2\nline 3\nline 4"
    assert (bounded.start_line, bounded.end_line, bounded.truncated) == (2, 4, True)

    full = read_symbol_source(tmp_path, wide, max_lines=40)
    assert full is not None
    assert full.truncated is False
    assert full.end_line == 9

    missing = read_symbol_source(tmp_path / "elsewhere", wide, max_lines=3)
    assert missing is None

    stale = Symbol(
        id="py:stale",
        language="python",
        kind=SymbolKind.FUNCTION,
        native_kind="function_definition",
        name="stale",
        qualified_name="stale",
        file_path="app/one.py",
        container_id=None,
        start_line=99,
        end_line=120,
        start_byte=0,
        end_byte=10,
        signature=None,
        source="tree-sitter",
        confidence=Confidence.HIGH,
    )
    assert read_symbol_source(tmp_path, stale, max_lines=3) is None
