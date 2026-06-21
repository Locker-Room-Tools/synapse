"""SQLite-backed symbol index."""

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from synapse.core.models import Confidence, Relation, RelationKind, SourceFile, Symbol, SymbolKind

DEFAULT_DB_NAME = "index.sqlite"

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
"""


def _map_source_file(row: sqlite3.Row) -> SourceFile:
    return SourceFile(
        id=str(row["id"]),
        path=str(row["path"]),
        language=str(row["language"]),
        project_root=row["project_root"],
        content_hash=str(row["content_hash"]),
        indexed_at=str(row["indexed_at"]),
    )


def _map_symbol(row: sqlite3.Row) -> Symbol:
    return Symbol(
        id=str(row["id"]),
        language=str(row["language"]),
        kind=SymbolKind(str(row["kind"])),
        native_kind=str(row["native_kind"]),
        name=str(row["name"]),
        qualified_name=row["qualified_name"],
        file_path=str(row["file_path"]),
        container_id=row["container_id"],
        start_line=int(row["start_line"]),
        end_line=int(row["end_line"]),
        start_byte=int(row["start_byte"]),
        end_byte=int(row["end_byte"]),
        signature=row["signature"],
        source=str(row["source"]),
        confidence=Confidence(str(row["confidence"])),
    )


def _map_relation(row: sqlite3.Row) -> Relation:
    return Relation(
        id=str(row["id"]),
        kind=RelationKind(str(row["kind"])),
        from_symbol_id=row["from_symbol_id"],
        to_symbol_id=row["to_symbol_id"],
        from_file_path=str(row["from_file_path"]),
        to_file_path=row["to_file_path"],
        to_name=row["to_name"],
        source=str(row["source"]),
        confidence=Confidence(str(row["confidence"])),
    )


def symbol_summary(symbol: Symbol) -> dict[str, object]:
    """Return the compact public representation for a symbol."""
    return {
        "symbol_id": symbol.id,
        "language": symbol.language,
        "kind": str(symbol.kind),
        "name": symbol.name,
        "qualified_name": symbol.qualified_name,
        "file_path": symbol.file_path,
        "line_range": [symbol.start_line, symbol.end_line],
        "signature": symbol.signature,
        "confidence": str(symbol.confidence),
    }


def relation_summary(relation: Relation) -> dict[str, object]:
    """Return the compact public representation for a relation."""
    return {
        "kind": str(relation.kind),
        "from_symbol_id": relation.from_symbol_id,
        "to_symbol_id": relation.to_symbol_id,
        "to_name": relation.to_name,
        "from_file_path": relation.from_file_path,
        "to_file_path": relation.to_file_path,
        "source": relation.source,
        "confidence": str(relation.confidence),
    }


def outline_item(symbol: Symbol) -> dict[str, object]:
    """Return the compact outline representation for a symbol."""
    return {
        "symbol_id": symbol.id,
        "kind": str(symbol.kind),
        "name": symbol.name,
        "line_range": [symbol.start_line, symbol.end_line],
        "children": [],
    }


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
            to_file_path, to_name, source, confidence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        row_list,
    )


def _name_stem(name: str) -> str:
    suffixes = ("Handler", "Endpoint", "Service", "Repository", "Command", "Query")
    for suffix in suffixes:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


class SymbolIndex:
    """Stores and queries symbols and relationships in a local SQLite database."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Open one transactional connection for multiple index writes."""
        with self._connect() as connection:
            yield connection

    def upsert_file(
        self,
        source_file: SourceFile,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Insert or update a tracked source file record."""
        if connection is None:
            with self._connect() as scoped_connection:
                self.upsert_file(source_file, connection=scoped_connection)
            return
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
        self,
        file_id: str,
        symbols: Iterable[Symbol],
        relations: Iterable[Relation] = (),
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Replace all extracted symbols and relations for one indexed file."""
        if connection is None:
            with self._connect() as scoped_connection:
                self.replace_symbols_for_file(
                    file_id,
                    symbols,
                    relations,
                    connection=scoped_connection,
                )
            return
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
                    source, confidence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                symbol_rows,
            )
        _insert_relation_rows(connection, relation_rows)

    def add_relations_for_file(
        self,
        file_id: str,
        relations: Iterable[Relation],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Refresh reference relations for one file without replacing symbols."""
        if connection is None:
            with self._connect() as scoped_connection:
                self.add_relations_for_file(
                    file_id,
                    relations,
                    connection=scoped_connection,
                )
            return
        relation_rows = _relation_rows(file_id, relations)
        connection.execute(
            "DELETE FROM relations WHERE file_id = ? AND kind = ?",
            (file_id, str(RelationKind.REFERENCES)),
        )
        _insert_relation_rows(connection, relation_rows)

    def remove_files(
        self,
        file_paths: Iterable[str],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """Remove tracked files and all derived rows by relative path."""
        paths = list(file_paths)
        if not paths:
            return 0
        placeholders = ", ".join("?" for _ in paths)
        if connection is None:
            with self._connect() as scoped_connection:
                return self.remove_files(paths, connection=scoped_connection)
        cursor = connection.execute(f"DELETE FROM files WHERE path IN ({placeholders})", paths)
        return cursor.rowcount

    def list_indexed_files(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[SourceFile]:
        """Return all indexed files."""
        if connection is None:
            with self._connect() as scoped_connection:
                return self.list_indexed_files(connection=scoped_connection)
        rows = connection.execute("SELECT * FROM files ORDER BY path").fetchall()
        return [_map_source_file(row) for row in rows]

    def upsert_symbols(self, symbols: Iterable[Symbol]) -> None:
        """Insert or update symbols."""
        symbol_list = list(symbols)
        file_ids = {symbol.file_path for symbol in symbol_list}
        if len(file_ids) != 1:
            msg = "upsert_symbols requires symbols for exactly one file"
            raise ValueError(msg)
        self.replace_symbols_for_file(symbol_list[0].file_path, symbol_list)

    def search_symbols(
        self,
        query: str,
        *,
        kind: str | SymbolKind | None = None,
        language: str | None = None,
        limit: int = 20,
    ) -> list[Symbol]:
        """Search symbols by name or qualified name."""
        normalized_limit = max(1, limit)
        where_clauses = ["(name LIKE ? OR COALESCE(qualified_name, '') LIKE ? OR file_path LIKE ?)"]
        params: list[object] = [f"%{query}%", f"%{query}%", f"%{query}%"]
        if kind is not None:
            where_clauses.append("kind = ?")
            params.append(str(kind))
        if language is not None:
            where_clauses.append("language = ?")
            params.append(language)
        params.extend([query, query, f"{query}%", normalized_limit])
        sql = f"""
            SELECT *
            FROM symbols
            WHERE {" AND ".join(where_clauses)}
            ORDER BY
                CASE WHEN name = ? THEN 0 ELSE 1 END,
                CASE WHEN qualified_name = ? THEN 0 ELSE 1 END,
                CASE WHEN name LIKE ? THEN 0 ELSE 1 END,
                length(name),
                start_line
            LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_map_symbol(row) for row in rows]

    def get_symbol(self, symbol_id: str) -> Symbol | None:
        """Return one symbol by its stable identifier."""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM symbols WHERE id = ?", (symbol_id,)).fetchone()
        return _map_symbol(row) if row is not None else None

    def get_definition(self, name: str) -> list[Symbol]:
        """Return symbol definition candidates for a name."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM symbols
                WHERE name = ? OR qualified_name = ?
                ORDER BY
                    CASE WHEN qualified_name = ? THEN 0 ELSE 1 END,
                    CASE WHEN name = ? THEN 0 ELSE 1 END,
                    length(COALESCE(qualified_name, name)),
                    start_line
                """,
                (name, name, name, name),
            ).fetchall()
        return [_map_symbol(row) for row in rows]

    def symbol_name_index(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, list[str]]:
        """Return a name-to-symbol-ids index for reference resolution."""
        result: dict[str, list[str]] = {}
        if connection is None:
            with self._connect() as scoped_connection:
                return self.symbol_name_index(connection=scoped_connection)
        rows = connection.execute(
            "SELECT id, name, qualified_name FROM symbols ORDER BY name, id"
        ).fetchall()
        for row in rows:
            symbol_id = str(row["id"])
            names = [str(row["name"])]
            qualified_name = row["qualified_name"]
            if qualified_name is not None:
                names.append(str(qualified_name))
            for name in names:
                symbol_ids = result.setdefault(name, [])
                if symbol_id not in symbol_ids:
                    symbol_ids.append(symbol_id)
        return result

    def workspace_stats(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, object]:
        """Return file/symbol counts and per-language percentages."""
        if connection is None:
            with self._connect() as scoped_connection:
                return self.workspace_stats(connection=scoped_connection)
        file_count = int(connection.execute("SELECT COUNT(*) FROM files").fetchone()[0])
        symbol_count = int(connection.execute("SELECT COUNT(*) FROM symbols").fetchone()[0])
        language_rows = connection.execute(
            """
            SELECT language, COUNT(*) AS c
            FROM files
            GROUP BY language
            ORDER BY c DESC, language
            """
        ).fetchall()
        languages = [
            {
                "language": str(row["language"]),
                "files": int(row["c"]),
                "percent": round((int(row["c"]) / file_count * 100) if file_count else 0.0, 2),
            }
            for row in language_rows
        ]
        return {"files": file_count, "symbols": symbol_count, "languages": languages}

    def project_map(self, top_symbols_limit: int = 20) -> dict[str, object]:
        """Return a compact directory tree of indexed files plus top symbols."""
        tree: dict[str, object] = {}
        for source_file in self.list_indexed_files():
            current = tree
            parts = source_file.path.split("/")
            for part in parts[:-1]:
                child = current.get(part)
                if not isinstance(child, dict):
                    child = {}
                    current[part] = child
                current = child
            current[parts[-1]] = None

        container_kinds = (
            SymbolKind.NAMESPACE,
            SymbolKind.CLASS,
            SymbolKind.STRUCT,
            SymbolKind.INTERFACE,
            SymbolKind.ENUM,
            SymbolKind.FUNCTION,
            SymbolKind.METHOD,
        )
        placeholders = ", ".join("?" for _ in container_kinds)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM symbols
                WHERE kind IN ({placeholders})
                ORDER BY
                    CASE kind
                        WHEN ? THEN 0
                        WHEN ? THEN 1
                        WHEN ? THEN 2
                        WHEN ? THEN 3
                        WHEN ? THEN 4
                        WHEN ? THEN 5
                        WHEN ? THEN 6
                        ELSE 7
                    END,
                    name,
                    file_path
                LIMIT ?
                """,
                [
                    *(str(kind) for kind in container_kinds),
                    *(str(kind) for kind in container_kinds),
                    max(1, top_symbols_limit),
                ],
            ).fetchall()
        return {
            "tree": tree,
            "top_symbols": [symbol_summary(_map_symbol(row)) for row in rows],
        }

    def get_file_outline(self, file_path: str) -> dict[str, object] | None:
        """Return the nested structural outline for one indexed file."""
        with self._connect() as connection:
            file_row = connection.execute(
                "SELECT * FROM files WHERE path = ?",
                (file_path,),
            ).fetchone()
            if file_row is None:
                return None
            symbol_rows = connection.execute(
                (
                    "SELECT * FROM symbols WHERE file_path = ? "
                    "ORDER BY start_byte, end_byte DESC, name"
                ),
                (file_path,),
            ).fetchall()
        symbols = [_map_symbol(row) for row in symbol_rows]
        items = {symbol.id: outline_item(symbol) for symbol in symbols}
        roots: list[dict[str, object]] = []
        for symbol in symbols:
            item = items[symbol.id]
            if symbol.container_id and symbol.container_id in items:
                parent_children = items[symbol.container_id]["children"]
                assert isinstance(parent_children, list)
                parent_children.append(item)
            else:
                roots.append(item)
        return {
            "file_path": file_path,
            "language": str(file_row["language"]),
            "symbols": roots,
        }

    def get_symbol_context(
        self,
        symbol_id: str,
        include_body: bool = False,
    ) -> dict[str, object] | None:
        """Return compact context around a symbol."""
        symbol = self.get_symbol(symbol_id)
        if symbol is None:
            return None
        with self._connect() as connection:
            child_rows = connection.execute(
                "SELECT * FROM symbols WHERE container_id = ? ORDER BY start_byte, name",
                (symbol.id,),
            ).fetchall()
            parent_row = None
            if symbol.container_id is not None:
                parent_row = connection.execute(
                    "SELECT * FROM symbols WHERE id = ?",
                    (symbol.container_id,),
                ).fetchone()
            file_row = connection.execute(
                "SELECT * FROM files WHERE path = ?",
                (symbol.file_path,),
            ).fetchone()
        body: str | None = None
        if include_body and file_row is not None and file_row["project_root"] is not None:
            absolute_path = Path(str(file_row["project_root"])) / symbol.file_path
            if absolute_path.exists():
                lines = absolute_path.read_text(encoding="utf-8", errors="replace").splitlines()
                body = "\n".join(lines[symbol.start_line - 1 : symbol.end_line])
        return {
            "symbol": symbol_summary(symbol),
            "parent": symbol_summary(_map_symbol(parent_row)) if parent_row is not None else None,
            "children": [symbol_summary(_map_symbol(row)) for row in child_rows],
            "body": body,
        }

    def get_dependencies(self, symbol_id: str) -> list[Relation]:
        """Return outgoing relations for a symbol."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM relations WHERE from_symbol_id = ? ORDER BY id",
                (symbol_id,),
            ).fetchall()
        return [_map_relation(row) for row in rows]

    def get_references(self, symbol_id: str) -> list[Relation]:
        """Return incoming resolved references for a symbol."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM relations
                WHERE kind = ? AND to_symbol_id = ?
                ORDER BY from_file_path, id
                """,
                (str(RelationKind.REFERENCES), symbol_id),
            ).fetchall()
        return [_map_relation(row) for row in rows]

    def get_references_by_name(self, name: str) -> list[Relation]:
        """Return incoming unresolved references for a symbol name."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM relations
                WHERE kind = ? AND to_name = ? AND to_symbol_id IS NULL
                ORDER BY from_file_path, id
                """,
                (str(RelationKind.REFERENCES), name),
            ).fetchall()
        return [_map_relation(row) for row in rows]

    def get_file_dependencies(self, file_path: str) -> dict[str, object] | None:
        """Return import dependencies declared in one indexed file."""
        with self._connect() as connection:
            file_row = connection.execute(
                "SELECT * FROM files WHERE path = ?", (file_path,)
            ).fetchone()
            if file_row is None:
                return None
            rows = connection.execute(
                """
                SELECT DISTINCT to_name
                FROM relations
                WHERE from_file_path = ? AND kind = ? AND to_name IS NOT NULL
                ORDER BY to_name
                """,
                (file_path, str(RelationKind.IMPORTS)),
            ).fetchall()
        return {"file_path": file_path, "imports": [str(row["to_name"]) for row in rows]}

    def find_references(
        self,
        *,
        symbol_id: str | None = None,
        name: str | None = None,
    ) -> dict[str, object]:
        """Return locations that reference a symbol by id or name."""
        relations_by_id: dict[str, Relation] = {}
        unresolved_names: set[str] = set()
        if symbol_id is not None:
            symbol = self.get_symbol(symbol_id)
            if symbol is not None:
                unresolved_names.add(symbol.name)
                for relation in self.get_references(symbol.id):
                    relations_by_id[relation.id] = relation
        elif name is not None:
            unresolved_names.add(name)
            for symbol in self.get_definition(name):
                for relation in self.get_references(symbol.id):
                    relations_by_id[relation.id] = relation

        for unresolved_name in unresolved_names:
            for relation in self.get_references_by_name(unresolved_name):
                relations_by_id[relation.id] = relation

        relations = sorted(
            relations_by_id.values(), key=lambda item: (item.from_file_path, item.id)
        )
        return {
            "items": [relation_summary(relation) for relation in relations],
            "files": sorted({relation.from_file_path for relation in relations}),
        }

    def related_symbols(self, symbol_id: str, limit: int = 20) -> dict[str, object] | None:
        """Return symbols structurally and semantically near a symbol."""
        symbol = self.get_symbol(symbol_id)
        if symbol is None:
            return None

        related: dict[str, tuple[int, Symbol]] = {}

        def add_related(candidate: Symbol, rank: int) -> None:
            if candidate.id == symbol.id:
                return
            existing = related.get(candidate.id)
            if existing is None or rank < existing[0]:
                related[candidate.id] = (rank, candidate)

        with self._connect() as connection:
            for relation in self.get_dependencies(symbol.id):
                if relation.kind is RelationKind.REFERENCES and relation.to_symbol_id is not None:
                    row = connection.execute(
                        "SELECT * FROM symbols WHERE id = ?", (relation.to_symbol_id,)
                    ).fetchone()
                    if row is not None:
                        add_related(_map_symbol(row), 1)
            for relation in self.get_references(symbol.id):
                if relation.from_symbol_id is not None:
                    row = connection.execute(
                        "SELECT * FROM symbols WHERE id = ?", (relation.from_symbol_id,)
                    ).fetchone()
                    if row is not None:
                        add_related(_map_symbol(row), 2)
            if symbol.container_id is None:
                sibling_rows = connection.execute(
                    """
                    SELECT * FROM symbols
                    WHERE file_path = ? AND container_id IS NULL AND id != ?
                    ORDER BY start_byte, name
                    """,
                    (symbol.file_path, symbol.id),
                ).fetchall()
            else:
                sibling_rows = connection.execute(
                    """
                    SELECT * FROM symbols
                    WHERE file_path = ? AND container_id = ? AND id != ?
                    ORDER BY start_byte, name
                    """,
                    (symbol.file_path, symbol.container_id, symbol.id),
                ).fetchall()
            for row in sibling_rows:
                add_related(_map_symbol(row), 3)

            stem = _name_stem(symbol.name)
            if stem and stem != symbol.name:
                stem_rows = connection.execute(
                    """
                    SELECT * FROM symbols
                    WHERE name LIKE ? AND id != ?
                    ORDER BY name, file_path
                    """,
                    (f"{stem}%", symbol.id),
                ).fetchall()
                for row in stem_rows:
                    add_related(_map_symbol(row), 4)

        ranked = sorted(related.values(), key=lambda item: (item[0], item[1].name, item[1].id))
        return {
            "symbol": symbol_summary(symbol),
            "related": [symbol_summary(candidate) for _, candidate in ranked[: max(1, limit)]],
        }

    def compact_context(self, symbol_id: str) -> dict[str, object] | None:
        """Return minimal context: file, dependencies, and related names."""
        symbol = self.get_symbol(symbol_id)
        if symbol is None:
            return None

        depends_on: list[str] = []
        for relation in self.get_dependencies(symbol.id):
            target_name = relation.to_name
            if target_name is None and relation.to_symbol_id is not None:
                target_symbol = self.get_symbol(relation.to_symbol_id)
                target_name = target_symbol.name if target_symbol is not None else None
            if target_name is not None and target_name not in depends_on:
                depends_on.append(target_name)

        related_names: list[str] = []
        related_payload = self.related_symbols(symbol.id, limit=10)
        raw_related = related_payload.get("related") if related_payload is not None else None
        if isinstance(raw_related, list):
            for item in raw_related:
                if isinstance(item, dict) and "name" in item:
                    related_names.append(str(item["name"]))
        return {
            "symbol": symbol_summary(symbol),
            "file": symbol.file_path,
            "depends_on": depends_on,
            "related": related_names,
        }
