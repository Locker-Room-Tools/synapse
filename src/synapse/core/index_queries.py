"""SQLite read projections and the symbol-index facade implementation."""

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from synapse.core.models import (
    Confidence,
    Relation,
    RelationKind,
    SourceFile,
    Symbol,
    SymbolKind,
)

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
DEFAULT_OUTLINE_LIMIT = 200
MAX_TOP_SYMBOLS_LIMIT = 50


def normalize_pagination(limit: int, offset: int) -> tuple[int, int]:
    """Clamp public pagination parameters to token-safe values."""
    return min(MAX_PAGE_LIMIT, max(1, limit)), max(0, offset)


def page_metadata(total: int, limit: int, offset: int, returned: int) -> dict[str, object]:
    """Return additive metadata for one deterministic result page."""
    return {
        "limit": limit,
        "offset": offset,
        "returned": returned,
        "total": total,
        "has_more": offset + returned < total,
    }


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


def _fts_prefix_query(query: str) -> str:
    escaped = query.replace('"', '""')
    return f'"{escaped}"*'


def _escape_like(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _name_stem(name: str) -> str:
    suffixes = ("Handler", "Endpoint", "Service", "Repository", "Command", "Query")
    for suffix in suffixes:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


class ReadProjections:
    """Read projections over one explicitly owned SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        yield self.connection

    def list_indexed_files(
        self,
    ) -> list[SourceFile]:
        """Return all indexed files."""
        rows = self.connection.execute("SELECT * FROM files ORDER BY path").fetchall()
        return [_map_source_file(row) for row in rows]

    def list_symbols_for_file(
        self,
        file_path: str,
    ) -> list[Symbol]:
        """Return all symbols currently indexed for one file."""
        rows = self.connection.execute(
            "SELECT * FROM symbols WHERE file_path = ? ORDER BY start_byte, end_byte, name",
            (file_path,),
        ).fetchall()
        return [_map_symbol(row) for row in rows]

    def search_symbols(
        self,
        query: str,
        *,
        kind: str | SymbolKind | None = None,
        language: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Symbol]:
        """Search symbols by name or qualified name (FTS prefix first, substring fallback)."""
        symbols, _ = self.search_symbols_page(
            query,
            kind=kind,
            language=language,
            limit=limit,
            offset=offset,
        )
        return symbols

    def search_symbols_page(
        self,
        query: str,
        *,
        kind: str | SymbolKind | None = None,
        language: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Symbol], dict[str, object]]:
        """Search symbols and return exact page metadata."""
        normalized_limit, normalized_offset = normalize_pagination(limit, offset)
        fetch_limit = normalized_offset + normalized_limit
        filter_clauses: list[str] = []
        filter_params: list[object] = []
        if kind is not None:
            filter_clauses.append("kind = ?")
            filter_params.append(str(kind))
        if language is not None:
            filter_clauses.append("language = ?")
            filter_params.append(language)
        ranking = """
            ORDER BY
                CASE WHEN name = ? THEN 0 ELSE 1 END,
                CASE WHEN qualified_name = ? THEN 0 ELSE 1 END,
                CASE WHEN name LIKE ? THEN 0 ELSE 1 END,
                length(name),
                start_line,
                file_path,
                id
        """
        ranking_params: list[object] = [query, query, f"{query}%"]

        with self._connection() as connection:
            filters = "".join(f" AND {clause}" for clause in filter_clauses)
            like = "%" + _escape_like(query) + "%"
            fts_sql = f"""
                SELECT symbols.*
                FROM symbols_fts
                JOIN symbols ON symbols.rowid = symbols_fts.rowid
                WHERE symbols_fts MATCH ?{filters}
                {ranking}
                LIMIT ?
            """
            fts_params = [
                _fts_prefix_query(query),
                *filter_params,
                *ranking_params,
                fetch_limit,
            ]
            try:
                rows = connection.execute(fts_sql, fts_params).fetchall()
            except sqlite3.OperationalError:
                rows = []
            symbols = [_map_symbol(row) for row in rows]

            if len(symbols) < fetch_limit:
                seen_ids = {symbol.id for symbol in symbols}
                like_sql = f"""
                    SELECT *
                    FROM symbols
                    WHERE (
                        name LIKE ? ESCAPE '\\'
                        OR COALESCE(qualified_name, '') LIKE ? ESCAPE '\\'
                        OR file_path LIKE ? ESCAPE '\\'
                    ){filters}
                    {ranking}
                    LIMIT ?
                """
                like_params = [
                    like,
                    like,
                    like,
                    *filter_params,
                    *ranking_params,
                    fetch_limit,
                ]
                for row in connection.execute(like_sql, like_params).fetchall():
                    symbol = _map_symbol(row)
                    if symbol.id not in seen_ids:
                        symbols.append(symbol)
                        if len(symbols) >= fetch_limit:
                            break
            count_sql = f"""
                SELECT COUNT(*)
                FROM symbols
                WHERE (
                    name LIKE ? ESCAPE '\\'
                    OR COALESCE(qualified_name, '') LIKE ? ESCAPE '\\'
                    OR file_path LIKE ? ESCAPE '\\'
                ){filters}
            """
            total = int(
                connection.execute(
                    count_sql,
                    [like, like, like, *filter_params],
                ).fetchone()[0]
            )
        page_items = symbols[normalized_offset : normalized_offset + normalized_limit]
        return page_items, page_metadata(
            total,
            normalized_limit,
            normalized_offset,
            len(page_items),
        )

    def get_symbol(self, symbol_id: str) -> Symbol | None:
        """Return one symbol by its stable identifier."""
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM symbols WHERE id = ?", (symbol_id,)).fetchone()
        return _map_symbol(row) if row is not None else None

    def get_definition(self, name: str) -> list[Symbol]:
        """Return symbol definition candidates for a name."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM symbols
                WHERE name = ? OR qualified_name = ?
                ORDER BY
                    CASE WHEN qualified_name = ? THEN 0 ELSE 1 END,
                    CASE WHEN name = ? THEN 0 ELSE 1 END,
                    length(COALESCE(qualified_name, name)),
                    start_line,
                    file_path,
                    id
                """,
                (name, name, name, name),
            ).fetchall()
        return [_map_symbol(row) for row in rows]

    def get_definition_page(
        self,
        name: str,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> tuple[list[Symbol], dict[str, object]]:
        """Return one page of definition candidates."""
        normalized_limit, normalized_offset = normalize_pagination(limit, offset)
        with self._connection() as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM symbols WHERE name = ? OR qualified_name = ?",
                    (name, name),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT *
                FROM symbols
                WHERE name = ? OR qualified_name = ?
                ORDER BY
                    CASE WHEN qualified_name = ? THEN 0 ELSE 1 END,
                    CASE WHEN name = ? THEN 0 ELSE 1 END,
                    length(COALESCE(qualified_name, name)),
                    start_line,
                    file_path,
                    id
                LIMIT ? OFFSET ?
                """,
                (name, name, name, name, normalized_limit, normalized_offset),
            ).fetchall()
        symbols = [_map_symbol(row) for row in rows]
        return symbols, page_metadata(
            total,
            normalized_limit,
            normalized_offset,
            len(symbols),
        )

    def symbol_name_index(
        self,
    ) -> dict[str, list[str]]:
        """Return a name-to-symbol-ids index for reference resolution."""
        result: dict[str, list[str]] = {}
        rows = self.connection.execute(
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

    def reference_source_files(
        self,
        names: Iterable[str],
    ) -> set[str]:
        """Return every indexed file containing a reference to one of the names."""
        ordered_names = sorted(set(names))
        if not ordered_names:
            return set()

        paths: set[str] = set()
        batch_size = 500
        for start in range(0, len(ordered_names), batch_size):
            batch = ordered_names[start : start + batch_size]
            placeholders = ", ".join("?" for _ in batch)
            rows = self.connection.execute(
                f"""
                SELECT DISTINCT file_id
                FROM relations
                WHERE kind = ? AND to_name IN ({placeholders})
                """,
                [str(RelationKind.REFERENCES), *batch],
            ).fetchall()
            paths.update(str(row["file_id"]) for row in rows)
        return paths

    def workspace_stats(
        self,
    ) -> dict[str, object]:
        """Return file/symbol counts and per-language percentages."""
        file_count = int(self.connection.execute("SELECT COUNT(*) FROM files").fetchone()[0])
        symbol_count = int(self.connection.execute("SELECT COUNT(*) FROM symbols").fetchone()[0])
        language_rows = self.connection.execute(
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

    def project_map(
        self,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
        top_symbols_limit: int = 20,
    ) -> dict[str, object]:
        """Return a compact directory tree of indexed files plus top symbols."""
        normalized_limit, normalized_offset = normalize_pagination(limit, offset)
        normalized_top_limit = min(MAX_TOP_SYMBOLS_LIMIT, max(1, top_symbols_limit))
        tree: dict[str, object] = {}
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
        with self._connection() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM files").fetchone()[0])
            file_rows = connection.execute(
                "SELECT path FROM files ORDER BY path LIMIT ? OFFSET ?",
                (normalized_limit, normalized_offset),
            ).fetchall()
            symbol_rows = connection.execute(
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
                    normalized_top_limit,
                ],
            ).fetchall()

        for row in file_rows:
            current = tree
            parts = str(row["path"]).split("/")
            for part in parts[:-1]:
                child = current.get(part)
                if not isinstance(child, dict):
                    child = {}
                    current[part] = child
                current = child
            current[parts[-1]] = None

        return {
            "tree": tree,
            "top_symbols": [symbol_summary(_map_symbol(row)) for row in symbol_rows],
            "page": page_metadata(
                total,
                normalized_limit,
                normalized_offset,
                len(file_rows),
            ),
        }

    def get_file_outline(
        self,
        file_path: str,
        *,
        max_symbols: int = DEFAULT_OUTLINE_LIMIT,
    ) -> dict[str, object] | None:
        """Return the nested structural outline for one indexed file."""
        normalized_limit, _ = normalize_pagination(max_symbols, 0)
        with self._connection() as connection:
            file_row = connection.execute(
                "SELECT * FROM files WHERE path = ?",
                (file_path,),
            ).fetchone()
            if file_row is None:
                return None
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM symbols WHERE file_path = ?",
                    (file_path,),
                ).fetchone()[0]
            )
            symbol_rows = connection.execute(
                (
                    "SELECT * FROM symbols WHERE file_path = ? "
                    "ORDER BY start_byte, end_byte DESC, name LIMIT ?"
                ),
                (file_path, normalized_limit),
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
            "returned": len(symbols),
            "total": total,
            "truncated": len(symbols) < total,
        }

    def get_symbol_context(
        self,
        symbol_id: str,
        include_body: bool = False,
        *,
        children_limit: int = DEFAULT_PAGE_LIMIT,
        children_offset: int = 0,
    ) -> dict[str, object] | None:
        """Return compact context around a symbol."""
        symbol = self.get_symbol(symbol_id)
        if symbol is None:
            return None
        normalized_limit, normalized_offset = normalize_pagination(
            children_limit,
            children_offset,
        )
        with self._connection() as connection:
            child_total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM symbols WHERE container_id = ?",
                    (symbol.id,),
                ).fetchone()[0]
            )
            child_rows = connection.execute(
                (
                    "SELECT * FROM symbols WHERE container_id = ? "
                    "ORDER BY start_byte, name LIMIT ? OFFSET ?"
                ),
                (symbol.id, normalized_limit, normalized_offset),
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
            "page": page_metadata(
                child_total,
                normalized_limit,
                normalized_offset,
                len(child_rows),
            ),
        }

    def get_dependencies(self, symbol_id: str) -> list[Relation]:
        """Return outgoing relations for a symbol."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM relations WHERE from_symbol_id = ? ORDER BY id",
                (symbol_id,),
            ).fetchall()
        return [_map_relation(row) for row in rows]

    def get_dependencies_page(
        self,
        symbol_id: str,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> tuple[list[Relation], dict[str, object]]:
        """Return one page of outgoing relations."""
        normalized_limit, normalized_offset = normalize_pagination(limit, offset)
        with self._connection() as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM relations WHERE from_symbol_id = ?",
                    (symbol_id,),
                ).fetchone()[0]
            )
            rows = connection.execute(
                ("SELECT * FROM relations WHERE from_symbol_id = ? ORDER BY id LIMIT ? OFFSET ?"),
                (symbol_id, normalized_limit, normalized_offset),
            ).fetchall()
        relations = [_map_relation(row) for row in rows]
        return relations, page_metadata(
            total,
            normalized_limit,
            normalized_offset,
            len(relations),
        )

    def get_references(self, symbol_id: str) -> list[Relation]:
        """Return incoming resolved references for a symbol."""
        with self._connection() as connection:
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
        with self._connection() as connection:
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

    def get_file_dependencies(
        self,
        file_path: str,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> dict[str, object] | None:
        """Return import dependencies declared in one indexed file."""
        normalized_limit, normalized_offset = normalize_pagination(limit, offset)
        with self._connection() as connection:
            file_row = connection.execute(
                "SELECT * FROM files WHERE path = ?", (file_path,)
            ).fetchone()
            if file_row is None:
                return None
            total = int(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT to_name)
                    FROM relations
                    WHERE from_file_path = ? AND kind = ? AND to_name IS NOT NULL
                    """,
                    (file_path, str(RelationKind.IMPORTS)),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT DISTINCT to_name
                FROM relations
                WHERE from_file_path = ? AND kind = ? AND to_name IS NOT NULL
                ORDER BY to_name
                LIMIT ? OFFSET ?
                """,
                (
                    file_path,
                    str(RelationKind.IMPORTS),
                    normalized_limit,
                    normalized_offset,
                ),
            ).fetchall()
        return {
            "file_path": file_path,
            "imports": [str(row["to_name"]) for row in rows],
            "page": page_metadata(
                total,
                normalized_limit,
                normalized_offset,
                len(rows),
            ),
        }

    def find_references(
        self,
        *,
        symbol_id: str | None = None,
        name: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> dict[str, object]:
        """Return locations that reference a symbol by id or name."""
        normalized_limit, normalized_offset = normalize_pagination(limit, offset)
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
        page_relations = relations[normalized_offset : normalized_offset + normalized_limit]
        return {
            "items": [relation_summary(relation) for relation in page_relations],
            "files": sorted({relation.from_file_path for relation in page_relations}),
            "page": page_metadata(
                len(relations),
                normalized_limit,
                normalized_offset,
                len(page_relations),
            ),
        }

    def related_symbols(
        self,
        symbol_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, object] | None:
        """Return symbols structurally and semantically near a symbol."""
        normalized_limit, normalized_offset = normalize_pagination(limit, offset)
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

        with self._connection() as connection:
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
        page_items = ranked[normalized_offset : normalized_offset + normalized_limit]
        return {
            "symbol": symbol_summary(symbol),
            "related": [symbol_summary(candidate) for _, candidate in page_items],
            "page": page_metadata(
                len(ranked),
                normalized_limit,
                normalized_offset,
                len(page_items),
            ),
        }

    def compact_context(self, symbol_id: str) -> dict[str, object] | None:
        """Return minimal context: file, dependencies, and related names."""
        symbol = self.get_symbol(symbol_id)
        if symbol is None:
            return None

        depends_on: list[str] = []
        for relation in self.get_dependencies(symbol.id)[:20]:
            target_name = relation.to_name
            if target_name is None and relation.to_symbol_id is not None:
                target_symbol = self.get_symbol(relation.to_symbol_id)
                target_name = target_symbol.name if target_symbol is not None else None
            if target_name is not None and target_name not in depends_on:
                depends_on.append(target_name)

        related_names: list[str] = []
        related_payload = self.related_symbols(symbol.id, limit=20)
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
