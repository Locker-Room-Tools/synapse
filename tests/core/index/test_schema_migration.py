"""Migrating an older index onto the enforced handle constraint.

The one-time backfill was never enough on its own: it repairs the rows that exist and
then stops, leaving the column nullable for whatever writes next. The migration must
also move the invariant into the file, and must not lose the FTS index while doing so.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from synapse.core.index import (
    SCHEMA_VERSION,
    SymbolIndex,
    integrity,
    is_symbol_handle,
    symbol_handle,
)
from tests.core.index.legacy_databases import (
    INVALID_ALPHABET_HANDLE,
    LEGACY_SYMBOL_INSERT,
    foreign_handle,
    legacy_symbol_row,
    null_handle_count,
    stored_handles,
    symbol_row_fields,
    write_legacy_database,
)

_SYMBOLS = [("py:one", "alpha"), ("py:two", "beta"), ("py:three", "gamma")]


@pytest.mark.parametrize(
    ("user_version", "with_handle_column"),
    [(4, False), (5, True)],
    ids=["from-v4-without-the-column", "from-v5-with-null-handles"],
)
def test_older_index_migrates_to_enforced_handles(
    tmp_path: Path,
    user_version: int,
    with_handle_column: bool,
) -> None:
    """Opening an older index backfills every handle and stamps the current version."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=user_version,
        symbols=_SYMBOLS,
        with_handle_column=with_handle_column,
    )

    index = SymbolIndex(database_path)

    assert null_handle_count(database_path) == 0
    assert stored_handles(database_path) == {
        symbol_id: symbol_handle(symbol_id) for symbol_id, _ in _SYMBOLS
    }
    with closing(sqlite3.connect(database_path)) as connection:
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
        indexes = {str(row[1]) for row in connection.execute("PRAGMA index_list(symbols)")}
    assert "idx_symbols_handle" in indexes

    # Every migrated row is reachable through the channel inspection actually uses.
    with index.read_session() as reads:
        found = reads.get_symbols_by_handles(
            [symbol_handle(symbol_id) for symbol_id, _ in _SYMBOLS]
        )
    assert len(found) == len(_SYMBOLS)


def test_migration_moves_the_invariant_into_the_file(tmp_path: Path) -> None:
    """After migrating, an older writer's handle-less insert is refused by the database."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=5,
        symbols=_SYMBOLS,
        with_handle_column=True,
    )

    SymbolIndex(database_path)

    # A daemon from an older build holding its own connection: the constraint is the
    # file's, not this runtime's, so it applies to that process too.
    with (
        closing(sqlite3.connect(database_path)) as connection,
        pytest.raises(sqlite3.IntegrityError, match="NOT NULL"),
    ):
        connection.execute(
            LEGACY_SYMBOL_INSERT,
            legacy_symbol_row("py:late", "late", file_path="app/legacy.py", line=42),
        )


def test_migration_preserves_full_text_search(tmp_path: Path) -> None:
    """The table rebuild reassigns rowids, so the external-content index must follow."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=5,
        symbols=_SYMBOLS,
        with_handle_column=True,
    )

    SymbolIndex(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            "SELECT s.name FROM symbols_fts f JOIN symbols s ON s.rowid = f.rowid "
            "WHERE symbols_fts MATCH 'alpha'"
        ).fetchall()
    assert [str(row[0]) for row in rows] == ["alpha"]


def test_reopening_a_migrated_index_changes_nothing(tmp_path: Path) -> None:
    """The migration is version-gated, so a second open is a no-op."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=5,
        symbols=_SYMBOLS,
        with_handle_column=True,
    )

    SymbolIndex(database_path)
    after_first = stored_handles(database_path)
    SymbolIndex(database_path)

    assert stored_handles(database_path) == after_first
    with closing(sqlite3.connect(database_path)) as connection:
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
        leftovers = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'symbols_migrating'"
        ).fetchall()
    assert leftovers == []


def test_duplicated_legacy_text_is_normalized_rather_than_treated_as_a_collision(
    tmp_path: Path,
) -> None:
    """Two rows sharing one wrong handle are each rewritten to their own derived value."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=5,
        symbols=_SYMBOLS[:2],
        with_handle_column=True,
        with_handle_index=False,
    )
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("UPDATE symbols SET handle = ?", (symbol_handle("py:one"),))

    SymbolIndex(database_path)

    assert stored_handles(database_path) == {
        symbol_id: symbol_handle(symbol_id) for symbol_id, _ in _SYMBOLS[:2]
    }


def test_a_genuine_digest_collision_fails_the_migration_rather_than_picking_a_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When two distinct ids derive the same handle, the migration aborts as one unit."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=5,
        symbols=_SYMBOLS[:2],
        with_handle_column=True,
        with_handle_index=False,
    )
    # A real 128-bit collision is unreachable by construction, so it is simulated at the
    # derivation the migration actually calls.
    monkeypatch.setattr(integrity, "symbol_handle", lambda symbol_id: "s_" + "C" * 22)

    with pytest.raises(sqlite3.IntegrityError):
        SymbolIndex(database_path)

    # The rebuild is one transaction, so the half-migrated table is never left behind.
    with closing(sqlite3.connect(database_path)) as connection:
        leftovers = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'symbols_migrating'"
        ).fetchall()
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    assert leftovers == []
    assert version == 5


def test_an_invalid_alphabet_legacy_handle_migrates_to_the_derived_value(
    tmp_path: Path,
) -> None:
    """Right length, right prefix, unusable characters -- a format test cannot see it."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=5,
        symbols=_SYMBOLS,
        with_handle_column=True,
        handles={"py:one": INVALID_ALPHABET_HANDLE},
    )
    assert not is_symbol_handle(INVALID_ALPHABET_HANDLE)

    SymbolIndex(database_path)

    assert stored_handles(database_path)["py:one"] == symbol_handle("py:one")


def test_a_legacy_handle_belonging_to_another_id_migrates_to_the_derived_value(
    tmp_path: Path,
) -> None:
    """A syntactically perfect handle for the wrong symbol is still unresolvable."""
    database_path = tmp_path / "index.sqlite"
    borrowed = foreign_handle("py:somewhere-else")
    write_legacy_database(
        database_path,
        user_version=5,
        symbols=_SYMBOLS,
        with_handle_column=True,
        handles={"py:two": borrowed},
    )
    assert is_symbol_handle(borrowed)
    assert borrowed != symbol_handle("py:two")

    SymbolIndex(database_path)

    assert stored_handles(database_path)["py:two"] == symbol_handle("py:two")


def test_migration_preserves_every_non_handle_field(tmp_path: Path) -> None:
    """Only the handle may change; nothing else about a symbol is rewritten."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=5,
        symbols=_SYMBOLS,
        with_handle_column=True,
        handles={"py:one": INVALID_ALPHABET_HANDLE},
    )
    before = symbol_row_fields(database_path, "py:one")

    SymbolIndex(database_path)

    after = symbol_row_fields(database_path, "py:one")
    assert set(after) == set(before)
    assert {key: value for key, value in after.items() if key != "handle"} == {
        key: value for key, value in before.items() if key != "handle"
    }
    assert after["handle"] == symbol_handle("py:one")


def test_reopening_after_normalization_is_a_no_op(tmp_path: Path) -> None:
    """The counterexample fixtures must also converge in one pass, not oscillate."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=5,
        symbols=_SYMBOLS,
        with_handle_column=True,
        handles={
            "py:one": INVALID_ALPHABET_HANDLE,
            "py:two": foreign_handle("py:somewhere-else"),
        },
    )

    SymbolIndex(database_path)
    after_first = stored_handles(database_path)
    SymbolIndex(database_path)

    assert stored_handles(database_path) == after_first
    with closing(sqlite3.connect(database_path)) as connection:
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
