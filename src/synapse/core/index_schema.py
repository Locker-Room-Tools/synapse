"""SQLite schema, connection lifecycle, and database replacement."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

SCHEMA_VERSION = 2

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    language TEXT NOT NULL,
    project_root TEXT,
    content_hash TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);

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
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);

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
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified_name ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);
CREATE INDEX IF NOT EXISTS idx_symbols_language ON symbols(language);
CREATE INDEX IF NOT EXISTS idx_symbols_file_id ON symbols(file_id);
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


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create or migrate the index schema on an open connection."""
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    connection.executescript(SCHEMA)
    if version < SCHEMA_VERSION:
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
