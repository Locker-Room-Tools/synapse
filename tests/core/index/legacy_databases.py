"""Literal pre-v6 index shapes, so migration fixtures never track the current DDL.

Written out by hand on purpose: a fixture built from the current schema could not
express a database whose `handle` column is absent, nullable, or unconstrained, which
is exactly the state these tests exist to reproduce.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

from synapse.core.index import symbol_handle

V4_SCHEMA = """
CREATE TABLE files (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    language TEXT NOT NULL,
    project_root TEXT,
    content_hash TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);

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
    confidence TEXT NOT NULL,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# The v5 shape: the column exists but is nullable, and the unique index does not
# constrain nulls because SQLite treats them as distinct.
V5_HANDLE_COLUMN = "ALTER TABLE symbols ADD COLUMN handle TEXT"
V5_HANDLE_INDEX = "CREATE UNIQUE INDEX idx_symbols_handle ON symbols(handle)"

# The write a pre-handle build performs: sixteen columns, no handle.
LEGACY_SYMBOL_INSERT = """
INSERT INTO symbols (
    id, file_id, language, kind, native_kind, name, qualified_name, file_path,
    container_id, start_line, end_line, start_byte, end_byte, signature, source, confidence
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


# The write the current contract performs: the handle travels with the row.
CURRENT_SYMBOL_INSERT = """
INSERT INTO symbols (
    id, file_id, language, kind, native_kind, name, qualified_name, file_path,
    container_id, start_line, end_line, start_byte, end_byte, signature, source,
    confidence, handle
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def legacy_symbol_row(
    symbol_id: str, name: str, *, file_path: str, line: int = 1
) -> tuple[object, ...]:
    """Return the sixteen values a pre-handle writer would bind."""
    return (
        symbol_id,
        file_path,
        "python",
        "function",
        "function_definition",
        name,
        name,
        file_path,
        None,
        line,
        line + 1,
        0,
        10,
        f"def {name}()",
        "tree-sitter",
        "high",
    )


def write_legacy_database(
    database_path: Path,
    *,
    user_version: int,
    symbols: list[tuple[str, str]],
    with_handle_column: bool,
    with_handle_index: bool = True,
    file_path: str = "app/legacy.py",
    reference_fingerprint: str | None = None,
    handles: dict[str, str] | None = None,
) -> None:
    """Create a pre-v6 index containing symbol rows written without handles.

    `handles` stamps specific stored text onto named rows, which is how the two
    legacy counterexamples are expressed: a value with the right length and prefix but
    invalid characters, and a well-formed handle belonging to a different symbol id.
    """
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.executescript(V4_SCHEMA)
        if with_handle_column:
            connection.execute(V5_HANDLE_COLUMN)
            if with_handle_index:
                connection.execute(V5_HANDLE_INDEX)
        connection.execute(
            "INSERT INTO files VALUES (?, ?, 'python', NULL, 'hash', '2026-01-01T00:00:00Z')",
            (file_path, file_path),
        )
        for line, (symbol_id, name) in enumerate(symbols, start=1):
            connection.execute(
                LEGACY_SYMBOL_INSERT,
                legacy_symbol_row(symbol_id, name, file_path=file_path, line=line),
            )
        for symbol_id, handle in (handles or {}).items():
            connection.execute("UPDATE symbols SET handle = ? WHERE id = ?", (handle, symbol_id))
        if reference_fingerprint is not None:
            connection.execute(
                "INSERT INTO index_meta (key, value) VALUES ('reference_fingerprint', ?)",
                (reference_fingerprint,),
            )
        connection.execute(f"PRAGMA user_version = {user_version}")


def null_handle_count(database_path: Path) -> int:
    """Count persisted symbol rows whose handle is missing."""
    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute("SELECT COUNT(*) FROM symbols WHERE handle IS NULL").fetchone()
    return int(row[0])


def stored_handles(database_path: Path) -> dict[str, str | None]:
    """Return every persisted symbol id mapped to its stored handle."""
    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute("SELECT id, handle FROM symbols").fetchall()
    return {str(row[0]): (None if row[1] is None else str(row[1])) for row in rows}


# A value with the expected length and prefix that `is_symbol_handle` still rejects: a
# format test cannot tell it apart from a real handle, but nothing can resolve it.
INVALID_ALPHABET_HANDLE = "s_" + "!" * 22


def foreign_handle(other_symbol_id: str) -> str:
    """A perfectly well-formed handle that belongs to a different stable id."""
    return symbol_handle(other_symbol_id)


def symbol_row_fields(database_path: Path, symbol_id: str) -> dict[str, object]:
    """Return every stored column for one symbol, so migration can be checked field-wise."""
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM symbols WHERE id = ?", (symbol_id,)).fetchone()
    return dict(row)
