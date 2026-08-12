"""Handle completeness: detection, deterministic repair, and honest corruption failure.

Orientation renders a handle from a stable id; inspection resolves it through the
persisted column. A row without a usable handle therefore produces a handle that cannot
round-trip, and a duplicated handle would let one arbitrary declaration answer for
another. Both are corruption, and both must be visible rather than plausible.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from synapse.core.index import (
    SCHEMA_VERSION,
    IndexIntegrityError,
    SymbolIndex,
    handle_completeness_reason,
    is_symbol_handle,
    repair_symbol_handles,
    symbol_handle,
)
from synapse.core.indexing import reference_extraction_fingerprint
from tests.core.index.legacy_databases import (
    CURRENT_SYMBOL_INSERT,
    LEGACY_SYMBOL_INSERT,
    legacy_symbol_row,
    null_handle_count,
    stored_handles,
    write_legacy_database,
)

_SYMBOLS = [("py:one", "alpha"), ("py:two", "beta"), ("py:three", "gamma")]


def test_missing_index_is_not_this_probes_concern(tmp_path: Path) -> None:
    """A missing database is reported by the caller's own earlier, cheaper check."""
    assert handle_completeness_reason(tmp_path / "absent.sqlite") is None


def test_null_handles_are_detected_on_an_otherwise_ready_index(tmp_path: Path) -> None:
    """Current schema, fresh fingerprint, and still unusable handles."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=SCHEMA_VERSION,
        symbols=_SYMBOLS,
        with_handle_column=True,
        reference_fingerprint=reference_extraction_fingerprint(),
    )

    assert null_handle_count(database_path) == len(_SYMBOLS)
    assert handle_completeness_reason(database_path) == "incomplete-handles"


def test_a_pre_current_schema_cannot_promise_future_completeness(tmp_path: Path) -> None:
    """An older file carries no constraint, so present completeness proves nothing."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=SCHEMA_VERSION - 1,
        symbols=_SYMBOLS,
        with_handle_column=True,
    )

    assert handle_completeness_reason(database_path) == "handles-unenforced"


def test_an_unreadable_database_is_a_repair_reason(tmp_path: Path) -> None:
    """Corruption is reported, not swallowed into a clean bill of health."""
    database_path = tmp_path / "index.sqlite"
    database_path.write_bytes(b"this is not a database")

    assert handle_completeness_reason(database_path) == "unreadable-index"


def test_the_probe_never_migrates_the_database_it_reads(tmp_path: Path) -> None:
    """It runs while a daemon may own the file, so it must leave the schema untouched."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=SCHEMA_VERSION - 1,
        symbols=_SYMBOLS,
        with_handle_column=False,
    )
    with closing(sqlite3.connect(database_path)) as connection:
        before = [tuple(row) for row in connection.execute("PRAGMA table_info(symbols)")]

    assert handle_completeness_reason(database_path) == "handles-unenforced"

    with closing(sqlite3.connect(database_path)) as connection:
        after = [tuple(row) for row in connection.execute("PRAGMA table_info(symbols)")]
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    assert after == before
    assert version == SCHEMA_VERSION - 1


def test_repair_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    """Every repaired handle is the pure function of its stable id; a rerun changes nothing."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=SCHEMA_VERSION - 1,
        symbols=_SYMBOLS,
        with_handle_column=True,
    )

    with closing(sqlite3.connect(database_path)) as connection, connection:
        repaired = repair_symbol_handles(connection)
    assert repaired == len(_SYMBOLS)
    assert null_handle_count(database_path) == 0
    assert stored_handles(database_path) == {
        symbol_id: symbol_handle(symbol_id) for symbol_id, _ in _SYMBOLS
    }

    first_pass = stored_handles(database_path)
    with closing(sqlite3.connect(database_path)) as connection, connection:
        assert repair_symbol_handles(connection) == 0
    assert stored_handles(database_path) == first_pass


def test_repair_rewrites_malformed_handles_too(tmp_path: Path) -> None:
    """A stored value of the wrong shape is as unusable as a missing one."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=SCHEMA_VERSION - 1,
        symbols=_SYMBOLS,
        with_handle_column=True,
    )
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("UPDATE symbols SET handle = 'garbage' WHERE id = 'py:one'")
        connection.execute(
            "UPDATE symbols SET handle = 'x_0123456789012345678901' WHERE id = ?", ("py:two",)
        )

    with closing(sqlite3.connect(database_path)) as connection, connection:
        assert repair_symbol_handles(connection) == len(_SYMBOLS)
    assert stored_handles(database_path) == {
        symbol_id: symbol_handle(symbol_id) for symbol_id, _ in _SYMBOLS
    }


def test_duplicate_persisted_handles_raise_instead_of_choosing(tmp_path: Path) -> None:
    """Two declarations behind one handle is corruption, never a lookup result."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=SCHEMA_VERSION,
        symbols=_SYMBOLS[:1],
        with_handle_column=True,
        # Without the unique index, a corrupt writer could persist a collision.
        with_handle_index=False,
    )
    collision = symbol_handle("py:one")
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute(
            LEGACY_SYMBOL_INSERT,
            legacy_symbol_row("py:impostor", "impostor", file_path="app/legacy.py", line=9),
        )
        connection.execute("UPDATE symbols SET handle = ?", (collision,))

    reads = _read_projections(database_path)
    with pytest.raises(IndexIntegrityError, match="multiple declarations"):
        reads.get_symbols_by_handles([collision])


def test_malformed_persisted_handle_raises(tmp_path: Path) -> None:
    """A stored value that is not handle-shaped cannot answer a handle lookup."""
    database_path = tmp_path / "index.sqlite"
    write_legacy_database(
        database_path,
        user_version=SCHEMA_VERSION,
        symbols=_SYMBOLS[:1],
        with_handle_column=True,
        with_handle_index=False,
    )
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("UPDATE symbols SET handle = 'not-a-handle'")

    reads = _read_projections(database_path)
    with pytest.raises(IndexIntegrityError, match="malformed"):
        reads.get_symbols_by_handles(["not-a-handle"])


def test_current_schema_rejects_a_duplicate_handle(tmp_path: Path) -> None:
    """The forward direction: the file itself refuses to store the collision."""
    index = SymbolIndex(tmp_path / "index.sqlite")
    with index.transaction() as connection:
        connection.execute(
            "INSERT INTO files VALUES ('a.py','a.py','python',NULL,'h','2026-01-01T00:00:00Z')"
        )
        connection.execute(
            CURRENT_SYMBOL_INSERT,
            (*legacy_symbol_row("py:one", "alpha", file_path="a.py"), symbol_handle("py:one")),
        )

    with (
        pytest.raises(sqlite3.IntegrityError),
        index.transaction() as connection,
    ):
        connection.execute(
            CURRENT_SYMBOL_INSERT,
            (
                *legacy_symbol_row("py:two", "beta", file_path="a.py", line=5),
                symbol_handle("py:one"),
            ),
        )


def test_current_schema_rejects_a_write_without_a_handle(tmp_path: Path) -> None:
    """A writer from an older build cannot reintroduce null handles into a current file."""
    index = SymbolIndex(tmp_path / "index.sqlite")
    with index.transaction() as connection:
        connection.execute(
            "INSERT INTO files VALUES ('a.py','a.py','python',NULL,'h','2026-01-01T00:00:00Z')"
        )

    with (
        pytest.raises(sqlite3.IntegrityError, match="NOT NULL"),
        index.transaction() as connection,
    ):
        connection.execute(
            LEGACY_SYMBOL_INSERT,
            legacy_symbol_row("py:one", "alpha", file_path="a.py"),
        )


def _read_projections(database_path: Path):  # type: ignore[no-untyped-def]
    """Read through a raw connection: `SymbolIndex` would migrate the fixture away."""
    from synapse.core.index.reads import ReadProjections

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return ReadProjections(connection)


@pytest.mark.parametrize(
    "handle",
    [
        "s_" + "!" * 22,
        "s_" + "A" * 21 + "+",
        "s_" + "A" * 21 + "/",
        "s_" + "A" * 21 + "=",
        "s_" + "A" * 21 + " ",
    ],
    ids=["punctuation", "plus", "slash", "padding", "space"],
)
def test_a_fresh_schema_rejects_same_length_handles_outside_the_alphabet(
    tmp_path: Path,
    handle: str,
) -> None:
    """The database enforces the format the public predicate defines, not just length.

    Each of these has the exact length and prefix a shape test would accept, so without
    the alphabet rule they would be stored and then fail to resolve.
    """
    assert not is_symbol_handle(handle)
    index = SymbolIndex(tmp_path / "index.sqlite")
    with index.transaction() as connection:
        connection.execute(
            "INSERT INTO files VALUES ('a.py','a.py','python',NULL,'h','2026-01-01T00:00:00Z')"
        )

    with (
        pytest.raises(sqlite3.IntegrityError),
        index.transaction() as connection,
    ):
        connection.execute(
            CURRENT_SYMBOL_INSERT,
            (*legacy_symbol_row("py:one", "alpha", file_path="a.py"), handle),
        )


def test_the_sql_constraint_and_the_public_predicate_agree(tmp_path: Path) -> None:
    """The DDL is generated from the same alphabet constant the regex uses."""
    index = SymbolIndex(tmp_path / "index.sqlite")
    with index.transaction() as connection:
        connection.execute(
            "INSERT INTO files VALUES ('a.py','a.py','python',NULL,'h','2026-01-01T00:00:00Z')"
        )

    accepted = "s_" + "aZ0_-" * 4 + "ab"
    assert is_symbol_handle(accepted)
    with index.transaction() as connection:
        connection.execute(
            CURRENT_SYMBOL_INSERT,
            (*legacy_symbol_row("py:one", "alpha", file_path="a.py"), accepted),
        )
    assert stored_handles(tmp_path / "index.sqlite") == {"py:one": accepted}
