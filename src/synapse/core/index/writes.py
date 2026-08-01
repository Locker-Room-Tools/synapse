"""Connection-explicit SQLite index writes."""

import sqlite3
from collections.abc import Iterable

from synapse.core.index.handles import symbol_handle
from synapse.core.models import Relation, RelationKind, SourceFile, Symbol


def _relation_rows(file_id: str, relations: Iterable[Relation]) -> list[tuple[object, ...]]:
    return [
        (
            relation.id,
            file_id,
            str(relation.kind),
            relation.from_symbol_id,
            relation.to_symbol_id,
            relation.from_file_path,
            relation.to_file_path,
            relation.to_name,
            relation.source,
            str(relation.confidence),
            relation.start_line,
            relation.start_byte_col,
            str(relation.resolution) if relation.resolution is not None else None,
            relation.usage_kind,
            relation.to_qualified_name,
        )
        for relation in relations
    ]


def _insert_relation_rows(
    connection: sqlite3.Connection,
    rows: Iterable[tuple[object, ...]],
) -> None:
    row_list = list(rows)
    if not row_list:
        return
    connection.executemany(
        """
        INSERT OR REPLACE INTO relations (
            id, file_id, kind, from_symbol_id, to_symbol_id, from_file_path,
            to_file_path, to_name, source, confidence, start_line, start_byte_col, resolution,
            usage_kind, to_qualified_name
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        row_list,
    )


def set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
    """Insert or update one index metadata entry."""
    connection.execute("INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)", (key, value))


def upsert_file(connection: sqlite3.Connection, source_file: SourceFile) -> None:
    """Insert or update a tracked source file record."""
    connection.execute(
        """
        INSERT INTO files (id, path, language, project_root, content_hash, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            id = excluded.id,
            language = excluded.language,
            project_root = excluded.project_root,
            content_hash = excluded.content_hash,
            indexed_at = excluded.indexed_at
        """,
        (
            source_file.id,
            source_file.path,
            source_file.language,
            source_file.project_root,
            source_file.content_hash,
            source_file.indexed_at,
        ),
    )


def replace_symbols_for_file(
    connection: sqlite3.Connection,
    file_id: str,
    symbols: Iterable[Symbol],
    relations: Iterable[Relation] = (),
) -> None:
    """Replace all extracted symbols and relations for one indexed file."""
    symbol_rows = [
        (
            symbol.id,
            file_id,
            symbol.language,
            str(symbol.kind),
            symbol.native_kind,
            symbol.name,
            symbol.qualified_name,
            symbol.file_path,
            symbol.container_id,
            symbol.start_line,
            symbol.end_line,
            symbol.start_byte,
            symbol.end_byte,
            symbol.signature,
            symbol.source,
            str(symbol.confidence),
            symbol_handle(symbol.id),
        )
        for symbol in symbols
    ]
    relation_rows = _relation_rows(file_id, relations)
    connection.execute("DELETE FROM relations WHERE file_id = ?", (file_id,))
    connection.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))
    if symbol_rows:
        connection.executemany(
            """
            INSERT INTO symbols (
                id, file_id, language, kind, native_kind, name, qualified_name, file_path,
                container_id, start_line, end_line, start_byte, end_byte, signature,
                source, confidence, handle
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            symbol_rows,
        )
    _insert_relation_rows(connection, relation_rows)


def add_relations_for_file(
    connection: sqlite3.Connection,
    file_id: str,
    relations: Iterable[Relation],
) -> None:
    """Refresh reference relations for one file without replacing symbols."""
    relation_rows = _relation_rows(file_id, relations)
    connection.execute(
        "DELETE FROM relations WHERE file_id = ? AND kind = ?",
        (file_id, str(RelationKind.REFERENCES)),
    )
    _insert_relation_rows(connection, relation_rows)


def remove_files(connection: sqlite3.Connection, file_paths: Iterable[str]) -> int:
    """Remove tracked files and all derived rows by relative path."""
    paths = list(file_paths)
    if not paths:
        return 0
    placeholders = ", ".join("?" for _ in paths)
    cursor = connection.execute(f"DELETE FROM files WHERE path IN ({placeholders})", paths)
    return cursor.rowcount
