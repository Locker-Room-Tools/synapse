"""SQLite read projections and the symbol-index facade implementation."""

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from synapse.core.languages import reference_extraction as get_reference_extraction
from synapse.core.languages import reference_limitations as get_reference_limitations
from synapse.core.languages import reference_usage_kinds as get_reference_usage_kinds
from synapse.core.models import (
    Confidence,
    Relation,
    RelationKind,
    ResolutionMethod,
    SourceFile,
    Symbol,
    SymbolKind,
)

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
DEFAULT_OUTLINE_LIMIT = 200
MAX_TOP_SYMBOLS_LIMIT = 50
DEFAULT_MAX_BODY_LINES = 200
MAX_CANDIDATE_IDS = 8
NAMESPACE_SUMMARY_LIMIT = 20


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


def _optional_int_column(row: sqlite3.Row, column: str) -> int | None:
    if column not in row.keys():  # noqa: SIM118 - sqlite3.Row supports no `in row`
        return None
    value = row[column]
    return int(value) if value is not None else None


def _optional_text_column(row: sqlite3.Row, column: str) -> str | None:
    if column not in row.keys():  # noqa: SIM118 - sqlite3.Row supports no `in row`
        return None
    value = row[column]
    return str(value) if value is not None else None


def _map_relation(row: sqlite3.Row) -> Relation:
    resolution = _optional_text_column(row, "resolution")
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
        start_line=_optional_int_column(row, "start_line"),
        start_byte_col=_optional_int_column(row, "start_byte_col"),
        resolution=ResolutionMethod(resolution) if resolution is not None else None,
        usage_kind=_optional_text_column(row, "usage_kind"),
        to_qualified_name=_optional_text_column(row, "to_qualified_name"),
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
    summary: dict[str, object] = {
        "kind": str(relation.kind),
        "from_symbol_id": relation.from_symbol_id,
        "to_symbol_id": relation.to_symbol_id,
        "to_name": relation.to_name,
        "from_file_path": relation.from_file_path,
        "to_file_path": relation.to_file_path,
        "line": relation.start_line,
        "byte_column": relation.start_byte_col,
        "source": relation.source,
        "confidence": str(relation.confidence),
    }
    if relation.usage_kind is not None:
        summary["usage_kind"] = relation.usage_kind
    if relation.to_qualified_name is not None:
        summary["to_qualified_name"] = relation.to_qualified_name
    return summary


# Meaningful declarations for a project overview, in relevance order: nameable types
# first, then callable entry points. Namespaces and imports are structural boilerplate
# and never appear — they are aggregated separately.
TOP_SYMBOL_KINDS: tuple[SymbolKind, ...] = (
    SymbolKind.CLASS,
    SymbolKind.RECORD,
    SymbolKind.INTERFACE,
    SymbolKind.STRUCT,
    SymbolKind.ENUM,
    SymbolKind.TYPE,
    SymbolKind.FUNCTION,
    SymbolKind.METHOD,
)


def _rank_top_symbols(
    rows_by_kind: dict[SymbolKind, list[sqlite3.Row]],
    limit: int,
) -> list[sqlite3.Row]:
    """Pick the page's top symbols with a deterministic per-kind cap.

    A strict kind cascade returns only classes in a class-heavy repository. The cap is an
    even share of the page across the kinds that actually have candidates, so a workspace
    with classes, records, and methods shows all three; unused quota falls back to the
    ranked remainder, so a single-kind workspace still fills the page. This is a cap, not
    a round-robin: low-information symbols are never promoted just to add variety.
    """
    populated = sum(1 for rows in rows_by_kind.values() if rows) or 1
    per_kind_cap = max(2, limit // populated)
    selected: list[sqlite3.Row] = []
    overflow: list[tuple[int, sqlite3.Row]] = []
    for rank, kind in enumerate(TOP_SYMBOL_KINDS):
        rows = rows_by_kind.get(kind, [])
        selected.extend(rows[:per_kind_cap])
        overflow.extend((rank, row) for row in rows[per_kind_cap:])

    ranks = {str(kind): rank for rank, kind in enumerate(TOP_SYMBOL_KINDS)}

    def sort_key(row: sqlite3.Row) -> tuple[int, str, str]:
        return (ranks[str(row["kind"])], str(row["name"]), str(row["file_path"]))

    selected.sort(key=sort_key)
    if len(selected) < limit:
        overflow.sort(
            key=lambda entry: (entry[0], str(entry[1]["name"]), str(entry[1]["file_path"]))
        )
        selected.extend(row for _, row in overflow[: limit - len(selected)])
        selected.sort(key=sort_key)
    return selected[:limit]


_MATCH_LABELS: dict[ResolutionMethod, str] = {
    ResolutionMethod.EXACT: "exact",
    ResolutionMethod.SCOPED: "scoped",
}


def _match_label(relation: Relation) -> str:
    """Return the public match tier for a confirmed reference relation."""
    if relation.resolution is None:
        return "heuristic"
    return _MATCH_LABELS.get(relation.resolution, "heuristic")


def outline_item(symbol: Symbol) -> dict[str, object]:
    """Return the compact outline representation for a symbol."""
    return {
        "symbol_id": symbol.id,
        "kind": str(symbol.kind),
        "name": symbol.name,
        "line_range": [symbol.start_line, symbol.end_line],
        "signature": symbol.signature,
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

    def symbol_resolution_facts(self) -> tuple[dict[str, str], dict[str, str]]:
        """Return (kind by symbol id, qualified name by symbol id) for reference binding.

        Symbols without a qualified name fall back to their simple name so every
        declaration is reachable through the resolver's dotted-suffix lookups.
        """
        kinds: dict[str, str] = {}
        qualified_names: dict[str, str] = {}
        rows = self.connection.execute(
            "SELECT id, kind, name, qualified_name FROM symbols ORDER BY id"
        ).fetchall()
        for row in rows:
            symbol_id = str(row["id"])
            kinds[symbol_id] = str(row["kind"])
            qualified_name = row["qualified_name"]
            qualified_names[symbol_id] = (
                str(qualified_name) if qualified_name is not None else str(row["name"])
            )
        return kinds, qualified_names

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
        placeholders = ", ".join("?" for _ in TOP_SYMBOL_KINDS)
        with self._connection() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM files").fetchone()[0])
            file_rows = connection.execute(
                "SELECT path FROM files ORDER BY path LIMIT ? OFFSET ?",
                (normalized_limit, normalized_offset),
            ).fetchall()
            top_symbols_total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM symbols WHERE kind IN ({placeholders})",
                    [str(kind) for kind in TOP_SYMBOL_KINDS],
                ).fetchone()[0]
            )
            # Fetch each kind's best candidates separately so one populous kind cannot
            # monopolise the page; the interleave below stays deterministic.
            rows_by_kind: dict[SymbolKind, list[sqlite3.Row]] = {
                kind: connection.execute(
                    """
                    SELECT * FROM symbols
                    WHERE kind = ?
                    ORDER BY name, file_path, start_line
                    LIMIT ?
                    """,
                    (str(kind), normalized_top_limit),
                ).fetchall()
                for kind in TOP_SYMBOL_KINDS
            }
            symbol_rows = _rank_top_symbols(rows_by_kind, normalized_top_limit)
            namespace_total = int(
                connection.execute(
                    "SELECT COUNT(DISTINCT name) FROM symbols WHERE kind = ?",
                    (str(SymbolKind.NAMESPACE),),
                ).fetchone()[0]
            )
            namespace_rows = connection.execute(
                "SELECT DISTINCT name FROM symbols WHERE kind = ? ORDER BY name LIMIT ?",
                (str(SymbolKind.NAMESPACE), NAMESPACE_SUMMARY_LIMIT),
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

        namespace_names = [str(row["name"]) for row in namespace_rows]
        file_paths = [str(row["path"]) for row in file_rows]
        # `tree` and `page` describe this page of files; `top_symbols` and `namespaces`
        # are workspace-wide aggregates that repeat unchanged on every page.
        page = page_metadata(total, normalized_limit, normalized_offset, len(file_rows))
        page["files"] = file_paths
        return {
            "tree": tree,
            "top_symbols": [symbol_summary(_map_symbol(row)) for row in symbol_rows],
            "top_symbols_total": top_symbols_total,
            "top_symbols_truncated": top_symbols_total > len(symbol_rows),
            "namespaces": {
                "items": namespace_names,
                "total": namespace_total,
                "truncated": namespace_total > len(namespace_names),
            },
            "page": page,
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
        max_body_lines: int = DEFAULT_MAX_BODY_LINES,
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
        body_truncated = False
        if include_body and file_row is not None and file_row["project_root"] is not None:
            absolute_path = Path(str(file_row["project_root"])) / symbol.file_path
            if absolute_path.exists():
                lines = absolute_path.read_text(encoding="utf-8", errors="replace").splitlines()
                body_lines = lines[symbol.start_line - 1 : symbol.end_line]
                normalized_max = max(1, max_body_lines)
                if len(body_lines) > normalized_max:
                    body_lines = body_lines[:normalized_max]
                    body_truncated = True
                body = "\n".join(body_lines)
        return {
            "symbol": symbol_summary(symbol),
            "parent": symbol_summary(_map_symbol(parent_row)) if parent_row is not None else None,
            "children": [symbol_summary(_map_symbol(row)) for row in child_rows],
            "body": body,
            "body_truncated": body_truncated,
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

    def get_meta(self, key: str) -> str | None:
        """Return one index metadata value, or None when absent."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM index_meta WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row is not None else None

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

    def _languages_for_paths(self, paths: set[str]) -> list[str]:
        if not paths:
            return []
        ordered_paths = sorted(paths)
        placeholders = ", ".join("?" for _ in ordered_paths)
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT DISTINCT language FROM files WHERE path IN ({placeholders})",
                ordered_paths,
            ).fetchall()
        return sorted(str(row["language"]) for row in rows)

    def _workspace_languages(self) -> list[str]:
        with self._connection() as connection:
            rows = connection.execute("SELECT DISTINCT language FROM files").fetchall()
        return sorted(str(row["language"]) for row in rows)

    def _reference_coverage(
        self,
        languages: list[str],
        counts: dict[str, int],
        *,
        zero_result: bool,
    ) -> dict[str, object]:
        extraction = [
            {
                "language": language,
                "completeness": str(get_reference_extraction(language)),
                "usage_kinds": list(get_reference_usage_kinds(language)),
                "limitations": list(get_reference_limitations(language)),
            }
            for language in languages
        ]
        coverage: dict[str, object] = {
            "resolution_model": "syntactic-structural",
            "exhaustive": False,
            "extraction": extraction,
            "counts": counts,
        }
        if zero_result:
            coverage["zero_result"] = "no-indexed-matches"
        return coverage

    def find_references(
        self,
        *,
        symbol_id: str | None = None,
        name: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> dict[str, object]:
        """Return references to a symbol: confirmed items plus same-name possible items.

        Resolution is syntactic and name-based. `items` holds relations bound to the
        target(s) by a unique-name heuristic; `possible_items` holds same-name relations
        whose target is ambiguous or unresolved and must never be read as confirmed usages.

        Both collections are paged by the same `limit`/`offset`, each reporting its own
        page block (`page` and `possible_page`). `files` aggregates every matched path
        across the full result, not just the current page; `page.files` is the page-scoped
        view. `coverage.counts` is likewise global, so it stays comparable across pages.
        """
        normalized_limit, normalized_offset = normalize_pagination(limit, offset)
        confirmed_by_id: dict[str, Relation] = {}
        possible_by_id: dict[str, tuple[Relation, list[str]]] = {}
        zero_result_languages: list[str] | None = None

        if symbol_id is not None:
            symbol = self.get_symbol(symbol_id)
            if symbol is not None:
                zero_result_languages = [symbol.language]
                for relation in self.get_references(symbol.id):
                    confirmed_by_id[relation.id] = relation
                candidate_ids = [candidate.id for candidate in self.get_definition(symbol.name)]
                if symbol.id in candidate_ids:
                    for relation in self.get_references_by_name(symbol.name):
                        possible_by_id[relation.id] = (relation, candidate_ids)
        elif name is not None:
            for definition in self.get_definition(name):
                for relation in self.get_references(definition.id):
                    confirmed_by_id[relation.id] = relation
            candidate_ids = [candidate.id for candidate in self.get_definition(name)]
            for relation in self.get_references_by_name(name):
                possible_by_id[relation.id] = (relation, candidate_ids)

        def sort_key(relation: Relation) -> tuple[str, int, int, str]:
            # Source order within a file, not lexicographic order of the synthetic id.
            return (
                relation.from_file_path,
                relation.start_line if relation.start_line is not None else 0,
                relation.start_byte_col if relation.start_byte_col is not None else 0,
                relation.id,
            )

        confirmed = sorted(confirmed_by_id.values(), key=sort_key)
        possible = sorted(possible_by_id.values(), key=lambda entry: sort_key(entry[0]))

        page_confirmed = confirmed[normalized_offset : normalized_offset + normalized_limit]
        # Ambiguous results are paged by the same window, so callers can walk past page one.
        shown_possible = possible[normalized_offset : normalized_offset + normalized_limit]

        items = [
            {**relation_summary(relation), "match": _match_label(relation)}
            for relation in page_confirmed
        ]
        possible_items: list[dict[str, object]] = []
        # Ambiguity is read from the persisted resolution, not recomputed from whatever
        # definitions happen to match at query time.
        ambiguous_total = sum(
            1 for relation, _ in possible if relation.resolution is not ResolutionMethod.UNRESOLVED
        )
        unresolved_total = len(possible) - ambiguous_total
        for relation, candidates in shown_possible:
            summary = relation_summary(relation)
            if relation.resolution is ResolutionMethod.UNRESOLVED:
                summary["match"] = "unresolved"
            else:
                summary["match"] = "ambiguous"
                summary["candidate_symbol_ids"] = candidates[:MAX_CANDIDATE_IDS]
                summary["candidate_count"] = len(candidates)
                summary["candidates_truncated"] = len(candidates) > MAX_CANDIDATE_IDS
            possible_items.append(summary)

        exact_total = sum(
            1 for relation in confirmed if relation.resolution is ResolutionMethod.EXACT
        )
        scoped_total = sum(
            1 for relation in confirmed if relation.resolution is ResolutionMethod.SCOPED
        )
        counts = {
            "exact": exact_total,
            "scoped": scoped_total,
            "heuristic": len(confirmed) - exact_total - scoped_total,
            "ambiguous": ambiguous_total,
            "unresolved": unresolved_total,
            # Compatibility alias for the original counter; only proven bindings count.
            "resolved": exact_total,
        }
        # `files` answers "which files does this symbol appear in", so it spans the whole
        # result; the page-scoped view lives under page.files.
        matched_paths = {relation.from_file_path for relation in confirmed}
        matched_paths.update(relation.from_file_path for relation, _ in possible)
        page_paths = {relation.from_file_path for relation in page_confirmed}
        page_paths.update(relation.from_file_path for relation, _ in shown_possible)
        zero_result = not confirmed and not possible
        if zero_result:
            languages = (
                zero_result_languages
                if zero_result_languages is not None
                else self._workspace_languages()
            )
        else:
            languages = self._languages_for_paths(
                {relation.from_file_path for relation in confirmed}
                | {relation.from_file_path for relation, _ in possible}
            )
        page = page_metadata(
            len(confirmed),
            normalized_limit,
            normalized_offset,
            len(page_confirmed),
        )
        page["files"] = sorted(page_paths)
        return {
            "items": items,
            "possible_items": possible_items,
            # Retained for compatibility; identical to possible_page["total"].
            "possible_total": len(possible),
            "files": sorted(matched_paths),
            "coverage": self._reference_coverage(languages, counts, zero_result=zero_result),
            "page": page,
            "possible_page": page_metadata(
                len(possible),
                normalized_limit,
                normalized_offset,
                len(shown_possible),
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
