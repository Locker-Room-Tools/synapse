"""The full-text branch must actually execute, not silently degrade to LIKE.

`symbols` and `symbols_fts` both declare `name`, `qualified_name`, and `file_path`, so an
unqualified column in the joined statement makes SQLite raise `ambiguous column name`.
That error used to be swallowed into an empty result, which meant every search fell
through to the substring fallback while every result-only test kept passing. These tests
assert on the statements that ran, not just on the rows that came back.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from synapse.core.index import SymbolIndex
from synapse.core.index.reads import ReadProjections, _fts_rows
from synapse.core.models import SymbolKind
from tests.core.navigation.builders import add_file, build_index, make_symbol


class _TracingConnection:
    """Records every statement and how many rows it returned."""

    def __init__(self, inner: sqlite3.Connection) -> None:
        self.inner = inner
        self.executed: list[tuple[str, int]] = []

    def execute(self, sql: str, params: Any = ()) -> Any:
        cursor = self.inner.execute(sql, params)
        rows = cursor.fetchall()
        self.executed.append((sql, len(rows)))
        return _Replayed(rows)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class _Replayed:
    """Serves already-fetched rows, so tracing does not consume the cursor."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None


def _fts_statements(tracer: _TracingConnection) -> list[tuple[str, int]]:
    return [entry for entry in tracer.executed if "symbols_fts" in entry[0]]


def _like_statements(tracer: _TracingConnection) -> list[tuple[str, int]]:
    return [
        entry
        for entry in tracer.executed
        if "symbols_fts" not in entry[0] and "COUNT(" not in entry[0]
    ]


@contextmanager
def _traced(index: SymbolIndex) -> Iterator[tuple[_TracingConnection, ReadProjections]]:
    """Yield projections whose statements are recorded, over a live read session."""
    with index.read_session() as reads:
        tracer = _TracingConnection(reads.connection)
        yield tracer, ReadProjections(tracer)  # type: ignore[arg-type]


@pytest.fixture
def indexed(tmp_path: Path) -> SymbolIndex:
    index = build_index(tmp_path)
    add_file(
        index,
        "app/mod.py",
        [
            # Token-prefix match: FTS can satisfy this one.
            make_symbol("py:target", "handler_target", "app/mod.py", line=1),
            # "handler" sits mid-token, so only the substring fallback finds it.
            make_symbol("py:mid", "xhandler", "app/mod.py", line=2),
        ],
    )
    add_file(
        index,
        "other/mod.py",
        [make_symbol("py:outside", "handler_outside", "other/mod.py", line=1)],
    )
    return index


def test_unscoped_name_search_executes_the_fts_statement(indexed: SymbolIndex) -> None:
    """The FTS statement runs and returns rows, rather than raising into the fallback."""
    with _traced(indexed) as (tracer, reads):
        rows, _ = reads.search_symbol_names_page("handler", limit=10)

    fts = _fts_statements(tracer)
    assert len(fts) == 1, "the full-text branch must be attempted exactly once"
    assert fts[0][1] > 0, "the full-text statement returned no rows, so it did not run"
    assert "handler_target" in {symbol.name for symbol in rows}


def test_scoped_fts_query_does_not_raise_an_ambiguous_column_error(
    indexed: SymbolIndex,
) -> None:
    """The scope predicate names a column both joined tables declare."""
    with _traced(indexed) as (tracer, reads):
        rows, page = reads.search_symbol_names_page("handler", path_scope="app", limit=10)

    fts = _fts_statements(tracer)
    assert len(fts) == 1
    assert fts[0][1] > 0
    assert {symbol.name for symbol in rows} == {"handler_target", "xhandler"}
    assert page["total"] == 2


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"language": "python"}, {"handler_target", "handler_outside", "xhandler"}),
        ({"kind": SymbolKind.FUNCTION}, {"handler_target", "handler_outside", "xhandler"}),
        ({"declarations_only": True}, {"handler_target", "handler_outside", "xhandler"}),
    ],
)
def test_filters_apply_inside_the_fts_branch(
    indexed: SymbolIndex,
    kwargs: dict[str, Any],
    expected: set[str],
) -> None:
    """Every filter is qualified, so none of them makes the FTS statement ambiguous."""
    with _traced(indexed) as (tracer, reads):
        rows, _ = reads.search_symbol_names_page("handler", limit=10, **kwargs)

    fts = _fts_statements(tracer)
    assert len(fts) == 1
    assert fts[0][1] > 0
    assert {symbol.name for symbol in rows} == expected


def test_substring_query_falls_back_because_fts_cannot_satisfy_it(
    indexed: SymbolIndex,
) -> None:
    """FTS matches token prefixes; a mid-token query legitimately finds nothing."""
    with _traced(indexed) as (tracer, reads):
        rows, _ = reads.search_symbol_names_page("andler", limit=10)

    fts = _fts_statements(tracer)
    assert len(fts) == 1
    # The statement ran and simply matched nothing — that is the fallback's purpose.
    assert fts[0][1] == 0
    assert _like_statements(tracer), "the substring fallback must run"
    assert {symbol.name for symbol in rows} == {"handler_target", "xhandler", "handler_outside"}


def test_fts_and_fallback_never_duplicate_a_symbol(indexed: SymbolIndex) -> None:
    """Both branches match `handler_target`; it must appear once."""
    with _traced(indexed) as (_, reads):
        rows, _ = reads.search_symbol_names_page("handler", limit=10)

    ids = [symbol.id for symbol in rows]
    assert len(ids) == len(set(ids))


def test_page_total_stays_the_like_semantic_total(indexed: SymbolIndex) -> None:
    """FTS bounds retrieval; the exact total still describes substring semantics."""
    with _traced(indexed) as (_, reads):
        _, page = reads.search_symbol_names_page("andler", limit=10)

    # No token starts with "andler", so an FTS-derived total would report zero.
    assert page["total"] == 3


def test_fts_helper_tolerates_a_malformed_query(tmp_path: Path) -> None:
    """A query FTS5 rejects degrades to the fallback; that path stays supported."""
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE symbols (id TEXT PRIMARY KEY, name TEXT);
        CREATE VIRTUAL TABLE symbols_fts USING fts5(name, content='symbols',
                                                    content_rowid='rowid');
        """
    )

    assert (
        _fts_rows(connection, "SELECT * FROM symbols_fts WHERE symbols_fts MATCH ?", ("AND",)) == []
    )


def test_fts_helper_reraises_a_statement_error(tmp_path: Path) -> None:
    """An ambiguous or missing column is this module's bug and must not be hidden.

    This is the exact failure that left the FTS branch dead: swallowing it made a broken
    statement indistinguishable from a query that legitimately matched nothing.
    """
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE symbols (id TEXT PRIMARY KEY, name TEXT);
        CREATE VIRTUAL TABLE symbols_fts USING fts5(name, content='symbols',
                                                    content_rowid='rowid');
        """
    )

    with pytest.raises(sqlite3.OperationalError, match="ambiguous column name"):
        _fts_rows(
            connection,
            "SELECT symbols.* FROM symbols_fts JOIN symbols ON symbols.rowid = "
            "symbols_fts.rowid WHERE symbols_fts MATCH ? ORDER BY CASE WHEN name = ? "
            "THEN 0 ELSE 1 END",
            ('"x"*', "x"),
        )
