"""SQLite schema, connection lifecycle, and database replacement."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

from synapse.core.index.contract import SCHEMA_VERSION
from synapse.core.index.handles import handle_check_sql
from synapse.core.index.integrity import repair_symbol_handles

__all__ = [
    "SCHEMA",
    "SCHEMA_VERSION",
    "atomic_replace_database",
    "cleanup_database_files",
    "connection_scope",
    "create_connection",
    "initialize_schema",
    "prepare_database_for_replacement",
    "temporary_database_path",
    "transaction_scope",
]

# Single source of truth for the symbols table: `SCHEMA` and the constraint migration
# must never drift. `handle` is NOT NULL and format-checked so the file itself rejects a
# writer that does not implement the current persistence contract, including an older
# daemon holding an open connection. The CHECK proves the format only; that a handle is
# the digest of its own symbol id is established by the write path and by migration.
SYMBOLS_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS symbols (
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
    handle TEXT NOT NULL CHECK ({handle_check_sql("handle")}),
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);
"""

SYMBOLS_HANDLE_INDEX_SQL = "CREATE UNIQUE INDEX IF NOT EXISTS idx_symbols_handle ON symbols(handle)"

SCHEMA = f"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    language TEXT NOT NULL,
    project_root TEXT,
    content_hash TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);
{SYMBOLS_TABLE_SQL}
CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    from_symbol_id TEXT,
    to_symbol_id TEXT,
    from_file_path TEXT NOT NULL,
    to_file_path TEXT,
    to_name TEXT,
    source TEXT NOT NULL,
    confidence TEXT NOT NULL,
    start_line INTEGER,
    start_byte_col INTEGER,
    resolution TEXT,
    usage_kind TEXT,
    to_qualified_name TEXT,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS index_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified_name ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);
CREATE INDEX IF NOT EXISTS idx_symbols_language ON symbols(language);
CREATE INDEX IF NOT EXISTS idx_symbols_file_id ON symbols(file_id);
{SYMBOLS_HANDLE_INDEX_SQL};
CREATE INDEX IF NOT EXISTS idx_relations_to_symbol_id ON relations(to_symbol_id);
CREATE INDEX IF NOT EXISTS idx_relations_from_symbol_id ON relations(from_symbol_id);
CREATE INDEX IF NOT EXISTS idx_relations_kind ON relations(kind);
CREATE INDEX IF NOT EXISTS idx_relations_to_name ON relations(to_name);
CREATE INDEX IF NOT EXISTS idx_relations_from_file_path ON relations(from_file_path);

CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    name, qualified_name, file_path,
    content='symbols',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS symbols_fts_after_insert AFTER INSERT ON symbols BEGIN
    INSERT INTO symbols_fts(rowid, name, qualified_name, file_path)
    VALUES (new.rowid, new.name, new.qualified_name, new.file_path);
END;

CREATE TRIGGER IF NOT EXISTS symbols_fts_after_delete AFTER DELETE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, qualified_name, file_path)
    VALUES ('delete', old.rowid, old.name, old.qualified_name, old.file_path);
END;

CREATE TRIGGER IF NOT EXISTS symbols_fts_after_update AFTER UPDATE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, qualified_name, file_path)
    VALUES ('delete', old.rowid, old.name, old.qualified_name, old.file_path);
    INSERT INTO symbols_fts(rowid, name, qualified_name, file_path)
    VALUES (new.rowid, new.name, new.qualified_name, new.file_path);
END;
"""


def create_connection(db_path: Path) -> sqlite3.Connection:
    """Create and configure one SQLite connection."""
    connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
    except Exception:
        connection.close()
        raise
    return connection


@contextmanager
def connection_scope(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Own and explicitly close one connection."""
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def transaction_scope(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit or roll back a connection and always close it."""
    with connection_scope(connection) as scoped_connection, scoped_connection:
        yield scoped_connection


_RELATION_COLUMN_MIGRATIONS = (
    ("start_line", "INTEGER"),
    ("start_byte_col", "INTEGER"),
    ("resolution", "TEXT"),
    ("usage_kind", "TEXT"),
    ("to_qualified_name", "TEXT"),
)


_SYMBOL_COLUMN_MIGRATIONS = (("handle", "TEXT"),)


def _migrate_columns(
    connection: sqlite3.Connection,
    table: str,
    migrations: tuple[tuple[str, str], ...],
) -> None:
    table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if table_exists is None:
        return
    existing = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    for column, column_type in migrations:
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


_SYMBOL_COPY_COLUMNS = (
    "id, file_id, language, kind, native_kind, name, qualified_name, file_path, "
    "container_id, start_line, end_line, start_byte, end_byte, signature, source, "
    "confidence, handle"
)


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _rebuild_symbols_table(connection: sqlite3.Connection) -> None:
    """Recreate `symbols` so the file enforces the handle constraint.

    SQLite cannot add NOT NULL/CHECK in place. The FTS triggers are dropped first so
    they do not follow the renamed table, and the copy assigns new rowids, so the
    caller must rebuild the external-content FTS index afterwards.

    Driven by explicit statements inside one transaction: `executescript` would commit
    the partially rebuilt table. The preceding handle repair may already have opened a
    transaction, in which case the rebuild simply joins it.
    """
    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("DROP TRIGGER IF EXISTS symbols_fts_after_insert")
        connection.execute("DROP TRIGGER IF EXISTS symbols_fts_after_delete")
        connection.execute("DROP TRIGGER IF EXISTS symbols_fts_after_update")
        connection.execute("ALTER TABLE symbols RENAME TO symbols_migrating")
        connection.execute(SYMBOLS_TABLE_SQL)
        connection.execute(
            f"INSERT INTO symbols ({_SYMBOL_COPY_COLUMNS}) "
            f"SELECT {_SYMBOL_COPY_COLUMNS} FROM symbols_migrating"
        )
        # Inside the rebuild, so a collision rolls the whole migration back instead of
        # surfacing later against a table that has already been committed. `SCHEMA`
        # recreates it with IF NOT EXISTS, which is then a no-op.
        connection.execute(SYMBOLS_HANDLE_INDEX_SQL)
        connection.execute("DROP TABLE symbols_migrating")
    except Exception:
        connection.rollback()
        raise
    connection.commit()


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create or migrate the index schema on an open connection."""
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    _migrate_columns(connection, "relations", _RELATION_COLUMN_MIGRATIONS)
    _migrate_columns(connection, "symbols", _SYMBOL_COLUMN_MIGRATIONS)
    if version < SCHEMA_VERSION and _table_exists(connection, "symbols"):
        # Repair while the constraint does not exist yet, then let the file carry it.
        repair_symbol_handles(connection)
        _rebuild_symbols_table(connection)
    connection.executescript(SCHEMA)
    if version < SCHEMA_VERSION:
        # After `executescript` has recreated the triggers, and after the table rebuild
        # invalidated every rowid the external-content index referred to.
        connection.execute("INSERT INTO symbols_fts(symbols_fts) VALUES ('rebuild')")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def temporary_database_path(target_db_path: Path) -> Path:
    """Create a closed same-directory path suitable for an atomic replacement."""
    target_db_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        prefix=f".{target_db_path.stem}.",
        suffix=".tmp",
        dir=target_db_path.parent,
        delete=False,
    ) as temporary_file:
        return Path(temporary_file.name)


def _sidecar_paths(db_path: Path) -> tuple[Path, ...]:
    suffixes = ("-journal", "-shm", "-wal")
    return tuple(db_path.with_name(f"{db_path.name}{suffix}") for suffix in suffixes)


def cleanup_database_files(db_path: Path) -> None:
    """Remove a SQLite database and its sidecar files."""
    db_path.unlink(missing_ok=True)
    for sidecar_path in _sidecar_paths(db_path):
        sidecar_path.unlink(missing_ok=True)


def prepare_database_for_replacement(db_path: Path) -> None:
    """Checkpoint WAL content into the main file and close all handles."""
    with connection_scope(create_connection(db_path)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        connection.execute("PRAGMA journal_mode = DELETE").fetchone()
    for sidecar_path in _sidecar_paths(db_path):
        sidecar_path.unlink(missing_ok=True)


def atomic_replace_database(source_db_path: Path, target_db_path: Path) -> None:
    """Atomically replace a database after checkpointing both file sets."""
    prepare_database_for_replacement(source_db_path)
    if target_db_path.exists():
        prepare_database_for_replacement(target_db_path)
    os.replace(source_db_path, target_db_path)
    for sidecar_path in _sidecar_paths(source_db_path):
        sidecar_path.unlink(missing_ok=True)
    for sidecar_path in _sidecar_paths(target_db_path):
        sidecar_path.unlink(missing_ok=True)
