"""SQLite read projections and the symbol-index facade implementation."""

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from synapse.core.index.handles import symbol_handle
from synapse.core.index.source import read_symbol_source
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
IN_CLAUSE_BATCH_SIZE = 500
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


def _sorted_batches(values: Iterable[str]) -> Iterator[list[str]]:
    """Yield deduplicated, sorted values in IN-clause-safe batches."""
    ordered = sorted(set(values))
    for start in range(0, len(ordered), IN_CLAUSE_BATCH_SIZE):
        yield ordered[start : start + IN_CLAUSE_BATCH_SIZE]


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
        "handle": symbol_handle(symbol.id),
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


def _fts_name_prefix_query(query: str) -> str:
    """Prefix query restricted to the declaration-name columns of ``symbols_fts``."""
    escaped = query.replace('"', '""')
    return f'{{name qualified_name}} : "{escaped}"*'


def _escape_like(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# SQLite prefixes FTS5 query-syntax errors with "fts5:". Schema and statement errors
# (an ambiguous or missing column, a broken join) carry no such prefix.
_FTS_QUERY_ERROR_PREFIX = "fts5:"


def _fts_rows(
    connection: sqlite3.Connection,
    sql: str,
    params: Sequence[object],
) -> list[sqlite3.Row]:
    """Run the full-text branch, tolerating only a malformed FTS query.

    A caller's term reaches ``MATCH`` quoted as a phrase, but FTS5 can still reject some
    inputs; those degrade to the substring fallback, which is the documented behaviour.
    Anything else is a defect in this module's SQL and must surface — swallowing it once
    left the FTS branch silently disabled while every result test kept passing.
    """
    try:
        return list(connection.execute(sql, params).fetchall())
    except sqlite3.OperationalError as exc:
        if not str(exc).startswith(_FTS_QUERY_ERROR_PREFIX):
            raise
        return []


def _path_scope_filter(
    path_scope: str | None,
    *,
    column: str = "file_path",
) -> tuple[str, list[object]]:
    """Return the SQL predicate restricting a path column to a workspace-relative prefix.

    Mirrors the navigation scope rule exactly — the path is the scope itself or lies
    under it — so a scoped query and an in-memory scope check can never disagree. The
    caller supplies an already-normalized relative scope.
    """
    if path_scope is None:
        return "", []
    clause = f"({column} = ? OR {column} LIKE ? ESCAPE '\\')"
    return clause, [path_scope, f"{_escape_like(path_scope)}/%"]


def term_is_path_like(term: str) -> bool:
    """Whether a term is shaped like a path, and so may match a path substring."""
    return "/" in term or "." in term


def _path_term_filter(term: str) -> tuple[str, list[object]]:
    """Return the SQL predicate matching one literal path term.

    The rule is shape-aware, and this is its single definition: a path-shaped term
    (containing a separator or an extension) matches an exact path, a ``/``-suffix, or a
    substring, while a bare word matches only an exact path or a whole trailing path
    component. Retrieval, the page total, the distinct-union count, and orientation's
    acceptance all read it from here — previously the SQL matched the broad set while
    orientation accepted the narrow one, so a response could report a term unmatched and
    simultaneously claim matching files were omitted.

    The exact branch binds the raw term because it is an equality test, while the LIKE
    branches bind the escaped form.
    """
    escaped = _escape_like(term)
    clauses = ["path = ?", "path LIKE ? ESCAPE '\\'"]
    params: list[object] = [term, f"%/{escaped}"]
    if term_is_path_like(term):
        clauses.append("path LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped}%")
    return " OR ".join(clauses), params


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
        """Search symbols by name, qualified name, or file path, with exact page metadata."""
        return self._search_page(
            query,
            match_file_path=True,
            kind=kind,
            language=language,
            limit=limit,
            offset=offset,
        )

    def search_symbol_names_page(
        self,
        query: str,
        *,
        kind: str | SymbolKind | None = None,
        language: str | None = None,
        path_scope: str | None = None,
        declarations_only: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Symbol], dict[str, object]]:
        """Search declarations by name or qualified name only, never by file path.

        ``search_symbols_page`` deliberately also matches ``file_path``, which makes it
        a combined name-and-path channel: on a large file a single bounded page can be
        filled entirely by path-only rows, hiding every real name match. This is the
        name-only half; literal path matching belongs to ``files_matching_path``. The
        reported total therefore agrees with ``symbol_name_match_count``.

        ``path_scope`` and ``declarations_only`` narrow the search space *before* the page
        bound, so out-of-scope rows and import statements can never consume the page and
        hide a real in-scope declaration.
        """
        return self._search_page(
            query,
            match_file_path=False,
            kind=kind,
            language=language,
            path_scope=path_scope,
            declarations_only=declarations_only,
            limit=limit,
            offset=offset,
        )

    def _search_page(
        self,
        query: str,
        *,
        match_file_path: bool,
        kind: str | SymbolKind | None = None,
        language: str | None = None,
        path_scope: str | None = None,
        declarations_only: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Symbol], dict[str, object]]:
        normalized_limit, normalized_offset = normalize_pagination(limit, offset)
        fetch_limit = normalized_offset + normalized_limit
        # Every fragment below is shared by the FTS branch, the LIKE fallback, and the
        # COUNT. The FTS branch joins `symbols_fts`, which declares `name`,
        # `qualified_name`, and `file_path` too, so every symbols column must be
        # qualified or SQLite rejects the statement as ambiguous.
        filter_clauses: list[str] = []
        filter_params: list[object] = []
        if kind is not None:
            filter_clauses.append("symbols.kind = ?")
            filter_params.append(str(kind))
        if declarations_only:
            filter_clauses.append("symbols.kind != ?")
            filter_params.append(str(SymbolKind.IMPORT))
        if language is not None:
            filter_clauses.append("symbols.language = ?")
            filter_params.append(language)
        # Bound with the rest, so the scope binds before LIMIT rather than after retrieval.
        scope_clause, scope_params = _path_scope_filter(path_scope, column="symbols.file_path")
        if scope_clause:
            filter_clauses.append(scope_clause)
            filter_params.extend(scope_params)
        ranking = """
            ORDER BY
                CASE WHEN symbols.name = ? THEN 0 ELSE 1 END,
                CASE WHEN symbols.qualified_name = ? THEN 0 ELSE 1 END,
                CASE WHEN symbols.name LIKE ? THEN 0 ELSE 1 END,
                length(symbols.name),
                symbols.start_line,
                symbols.file_path,
                symbols.id
        """
        ranking_params: list[object] = [query, query, f"{query}%"]

        match_clauses = [
            "symbols.name LIKE ? ESCAPE '\\'",
            "COALESCE(symbols.qualified_name, '') LIKE ? ESCAPE '\\'",
        ]
        if match_file_path:
            match_clauses.append("symbols.file_path LIKE ? ESCAPE '\\'")
        matches = "\n                        OR ".join(match_clauses)

        with self._connection() as connection:
            filters = "".join(f" AND {clause}" for clause in filter_clauses)
            like = "%" + _escape_like(query) + "%"
            like_values = [like] * len(match_clauses)
            fts_sql = f"""
                SELECT symbols.*
                FROM symbols_fts
                JOIN symbols ON symbols.rowid = symbols_fts.rowid
                WHERE symbols_fts MATCH ?{filters}
                {ranking}
                LIMIT ?
            """
            fts_query = (
                _fts_prefix_query(query) if match_file_path else _fts_name_prefix_query(query)
            )
            fts_params = [
                fts_query,
                *filter_params,
                *ranking_params,
                fetch_limit,
            ]
            rows = _fts_rows(connection, fts_sql, fts_params)
            symbols = [_map_symbol(row) for row in rows]

            if len(symbols) < fetch_limit:
                seen_ids = {symbol.id for symbol in symbols}
                like_sql = f"""
                    SELECT symbols.*
                    FROM symbols
                    WHERE (
                        {matches}
                    ){filters}
                    {ranking}
                    LIMIT ?
                """
                like_params = [
                    *like_values,
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
                    {matches}
                ){filters}
            """
            total = int(
                connection.execute(
                    count_sql,
                    [*like_values, *filter_params],
                ).fetchone()[0]
            )
        page_items = symbols[normalized_offset : normalized_offset + normalized_limit]
        return page_items, page_metadata(
            total,
            normalized_limit,
            normalized_offset,
            len(page_items),
        )

    def symbol_name_match_count(
        self,
        query: str,
        *,
        path_scope: str | None = None,
        declarations_only: bool = False,
    ) -> int:
        """Count symbols whose name or qualified name contains ``query``.

        Unlike ``search_symbols_page`` totals, path-only matches are excluded, so
        the count reflects how crowded a name actually is among declarations.
        ``path_scope`` and ``declarations_only`` must mirror whatever the paired page
        query used, so crowding is judged against the set that page retrieves from.
        """
        like = "%" + _escape_like(query) + "%"
        filters, params = self._symbol_filters(
            path_scope=path_scope, declarations_only=declarations_only
        )
        with self._connection() as connection:
            row = connection.execute(
                rf"""
                SELECT COUNT(*)
                FROM symbols
                WHERE (
                    symbols.name LIKE ? ESCAPE '\'
                    OR COALESCE(symbols.qualified_name, '') LIKE ? ESCAPE '\'
                ){filters}
                """,
                (like, like, *params),
            ).fetchone()
        return int(row[0])

    def declaration_count(self, *, path_scope: str | None = None) -> int:
        """Count searchable declarations, excluding imports and optionally scoped.

        This is the population the crowd threshold is measured against. It has to
        describe the same search space as the match count it is compared with, or a
        term that saturates a small scope inside a large workspace is never classified
        as crowded.
        """
        filters, params = self._symbol_filters(path_scope=path_scope, declarations_only=True)
        clause = filters.removeprefix(" AND ") or "1"
        with self._connection() as connection:
            row = connection.execute(f"SELECT COUNT(*) FROM symbols WHERE {clause}", params)
            return int(row.fetchone()[0])

    @staticmethod
    def _symbol_filters(
        *,
        path_scope: str | None,
        declarations_only: bool,
    ) -> tuple[str, list[object]]:
        """Return the shared ``symbols`` restrictions as an appendable AND-fragment."""
        clauses: list[str] = []
        params: list[object] = []
        if declarations_only:
            clauses.append("symbols.kind != ?")
            params.append(str(SymbolKind.IMPORT))
        scope_clause, scope_params = _path_scope_filter(path_scope, column="symbols.file_path")
        if scope_clause:
            clauses.append(scope_clause)
            params.extend(scope_params)
        return "".join(f" AND {clause}" for clause in clauses), params

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

    def get_definition_nocase(self, name: str, limit: int = 25) -> list[Symbol]:
        """Return definition candidates matching a name case-insensitively.

        Lets a lowercased question term ("key") reach a capitalized declaration
        ("Key"); ordering mirrors ``get_definition``.
        """
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM symbols
                WHERE name = ? COLLATE NOCASE OR qualified_name = ? COLLATE NOCASE
                ORDER BY
                    CASE WHEN qualified_name = ? COLLATE NOCASE THEN 0 ELSE 1 END,
                    CASE WHEN name = ? COLLATE NOCASE THEN 0 ELSE 1 END,
                    length(COALESCE(qualified_name, name)),
                    start_line,
                    file_path,
                    id
                LIMIT ?
                """,
                (name, name, name, name, limit),
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

    def get_symbols_by_ids(self, symbol_ids: Sequence[str]) -> dict[str, Symbol]:
        """Return the indexed symbols for the given ids; missing ids are omitted."""
        symbols: dict[str, Symbol] = {}
        for batch in _sorted_batches(symbol_ids):
            placeholders = ", ".join("?" for _ in batch)
            rows = self.connection.execute(
                f"SELECT * FROM symbols WHERE id IN ({placeholders})",
                batch,
            ).fetchall()
            for row in rows:
                symbol = _map_symbol(row)
                symbols[symbol.id] = symbol
        return symbols

    def get_symbols_by_handles(self, handles: Sequence[str]) -> dict[str, Symbol]:
        """Return indexed symbols keyed by compact handle; missing handles are omitted."""
        symbols: dict[str, Symbol] = {}
        for batch in _sorted_batches(handles):
            placeholders = ", ".join("?" for _ in batch)
            rows = self.connection.execute(
                f"SELECT * FROM symbols WHERE handle IN ({placeholders})",
                batch,
            ).fetchall()
            for row in rows:
                symbols[str(row["handle"])] = _map_symbol(row)
        return symbols

    def files_matching_path(
        self,
        term: str,
        *,
        path_scope: str | None = None,
        limit: int = 20,
    ) -> tuple[list[str], dict[str, object]]:
        """Return indexed file paths matching a literal path term, with page metadata.

        Ordered by match strength: exact relative path, then path suffix
        ("%/term"), then substring; ties break on path. The metadata carries the exact
        total, so a caller can report how many matches the ``limit`` withheld instead of
        mistaking the page for the whole result. ``path_scope`` is applied before the
        limit.
        """
        match_clause, match_params = _path_term_filter(term)
        scope_clause, scope_params = _path_scope_filter(path_scope, column="path")
        scope_sql = f" AND {scope_clause}" if scope_clause else ""
        escaped = _escape_like(term)
        normalized_limit = max(1, limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT path FROM files
                WHERE ({match_clause}){scope_sql}
                ORDER BY CASE
                    WHEN path = ? THEN 0
                    WHEN path LIKE ? ESCAPE '\\' THEN 1
                    ELSE 2
                END, path
                LIMIT ?
                """,
                (*match_params, *scope_params, term, f"%/{escaped}", normalized_limit),
            ).fetchall()
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM files WHERE ({match_clause}){scope_sql}",
                    (*match_params, *scope_params),
                ).fetchone()[0]
            )
        paths = [str(row["path"]) for row in rows]
        return paths, page_metadata(total, normalized_limit, 0, len(paths))

    def count_files_matching_paths(
        self,
        terms: Sequence[str],
        *,
        path_scope: str | None = None,
    ) -> int:
        """Count distinct indexed files matching any of the given literal path terms.

        Per-term totals cannot simply be summed, because one file may match several
        terms. Only a distinct union over all terms gives an omission count that is
        exactly the difference between what matched and what was returned.
        """
        cleaned = [term for term in dict.fromkeys(terms) if term]
        if not cleaned:
            return 0
        clauses: list[str] = []
        params: list[object] = []
        for term in cleaned:
            clause, term_params = _path_term_filter(term)
            clauses.append(f"({clause})")
            params.extend(term_params)
        scope_clause, scope_params = _path_scope_filter(path_scope, column="path")
        scope_sql = f" AND {scope_clause}" if scope_clause else ""
        params.extend(scope_params)
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT COUNT(DISTINCT path) FROM files WHERE ({' OR '.join(clauses)}){scope_sql}",
                params,
            ).fetchone()
        return int(row[0])

    def relations_from_symbols(
        self,
        symbol_ids: Sequence[str],
        *,
        kinds: tuple[RelationKind, ...],
    ) -> list[Relation]:
        """Return outgoing relations of the given kinds for a set of symbols."""
        return self._relations_for_symbols(symbol_ids, kinds=kinds, column="from_symbol_id")

    def relations_to_symbols(
        self,
        symbol_ids: Sequence[str],
        *,
        kinds: tuple[RelationKind, ...],
    ) -> list[Relation]:
        """Return incoming relations of the given kinds for a set of symbols."""
        return self._relations_for_symbols(symbol_ids, kinds=kinds, column="to_symbol_id")

    def _relations_for_symbols(
        self,
        symbol_ids: Sequence[str],
        *,
        kinds: tuple[RelationKind, ...],
        column: str,
    ) -> list[Relation]:
        if not kinds:
            return []
        kind_values = [str(kind) for kind in kinds]
        kind_placeholders = ", ".join("?" for _ in kind_values)
        relations: list[Relation] = []
        for batch in _sorted_batches(symbol_ids):
            placeholders = ", ".join("?" for _ in batch)
            rows = self.connection.execute(
                f"""
                SELECT * FROM relations
                WHERE {column} IN ({placeholders}) AND kind IN ({kind_placeholders})
                ORDER BY {column}, id
                """,
                [*batch, *kind_values],
            ).fetchall()
            relations.extend(_map_relation(row) for row in rows)
        return relations

    def trusted_incoming_degrees(self, limit: int) -> list[tuple[Symbol, int]]:
        """Return symbols by incoming exact/scoped reference count, best first.

        Only syntax-proven (`exact`) and scope-narrowed (`scoped`) references count:
        heuristic unique-name popularity is never a centrality signal. Ordered by
        count descending, then symbol id, so the projection is deterministic for one
        index state. Targets whose symbol row is missing are silently skipped.
        """
        normalized_limit = min(500, max(1, limit))
        rows = self.connection.execute(
            """
            SELECT to_symbol_id, COUNT(*) AS c
            FROM relations
            WHERE kind = ? AND to_symbol_id IS NOT NULL AND resolution IN (?, ?)
            GROUP BY to_symbol_id
            ORDER BY c DESC, to_symbol_id
            LIMIT ?
            """,
            [
                str(RelationKind.REFERENCES),
                str(ResolutionMethod.EXACT),
                str(ResolutionMethod.SCOPED),
                normalized_limit,
            ],
        ).fetchall()
        counts = {str(row["to_symbol_id"]): int(row["c"]) for row in rows}
        symbols = self.get_symbols_by_ids(list(counts))
        return [
            (symbols[symbol_id], count)
            for symbol_id, count in counts.items()
            if symbol_id in symbols
        ]

    def trusted_incoming_degrees_for_ids(self, symbol_ids: Sequence[str]) -> dict[str, int]:
        """Exact/scoped incoming reference counts for specific symbols.

        The same trust rule as ``trusted_incoming_degrees`` — heuristic popularity
        never counts — but scoped to the given ids, so modestly referenced symbols
        outside the workspace-wide top ranks still report their true degree.
        """
        counts: dict[str, int] = {}
        for batch in _sorted_batches(symbol_ids):
            placeholders = ", ".join("?" for _ in batch)
            rows = self.connection.execute(
                f"""
                SELECT to_symbol_id, COUNT(*) AS c
                FROM relations
                WHERE kind = ? AND resolution IN (?, ?) AND to_symbol_id IN ({placeholders})
                GROUP BY to_symbol_id
                ORDER BY to_symbol_id
                """,
                [
                    str(RelationKind.REFERENCES),
                    str(ResolutionMethod.EXACT),
                    str(ResolutionMethod.SCOPED),
                    *batch,
                ],
            ).fetchall()
            for row in rows:
                counts[str(row["to_symbol_id"])] = int(row["c"])
        return counts

    def containment_child_counts(self, limit: int) -> dict[str, int]:
        """Return per-container child counts from parser-proven containment edges."""
        normalized_limit = min(1000, max(1, limit))
        rows = self.connection.execute(
            """
            SELECT from_symbol_id, COUNT(*) AS c
            FROM relations
            WHERE kind = ? AND from_symbol_id IS NOT NULL
            GROUP BY from_symbol_id
            ORDER BY c DESC, from_symbol_id
            LIMIT ?
            """,
            [str(RelationKind.CONTAINS), normalized_limit],
        ).fetchall()
        return {str(row["from_symbol_id"]): int(row["c"]) for row in rows}

    def import_name_counts(self) -> dict[str, int]:
        """Return, per imported dotted name, how many distinct files declare the import.

        Declared module structure: each entry is an explicit import statement, so the
        counts measure public-surface reach, not name-matching popularity.
        """
        rows = self.connection.execute(
            """
            SELECT to_name, COUNT(DISTINCT from_file_path) AS c
            FROM relations
            WHERE kind = ? AND to_name IS NOT NULL
            GROUP BY to_name
            ORDER BY c DESC, to_name
            """,
            [str(RelationKind.IMPORTS)],
        ).fetchall()
        return {str(row["to_name"]): int(row["c"]) for row in rows}

    def top_declared_symbols(self, limit: int) -> list[Symbol]:
        """Return prominent declarations by kind relevance, name, and location."""
        normalized_limit = min(MAX_PAGE_LIMIT, max(1, limit))
        symbols: list[Symbol] = []
        for kind in TOP_SYMBOL_KINDS:
            rows = self.connection.execute(
                "SELECT * FROM symbols WHERE kind = ? ORDER BY name, file_path, start_line LIMIT ?",
                (str(kind), normalized_limit),
            ).fetchall()
            symbols.extend(_map_symbol(row) for row in rows)
            if len(symbols) >= normalized_limit:
                break
        return symbols[:normalized_limit]

    def imports_for_files(self, file_paths: Sequence[str]) -> dict[str, list[str]]:
        """Return the distinct imported names per file, ordered by name."""
        imports: dict[str, list[str]] = {}
        for batch in _sorted_batches(file_paths):
            placeholders = ", ".join("?" for _ in batch)
            rows = self.connection.execute(
                f"""
                SELECT DISTINCT from_file_path, to_name
                FROM relations
                WHERE kind = ? AND to_name IS NOT NULL AND from_file_path IN ({placeholders})
                ORDER BY from_file_path, to_name
                """,
                [str(RelationKind.IMPORTS), *batch],
            ).fetchall()
            for row in rows:
                imports.setdefault(str(row["from_file_path"]), []).append(str(row["to_name"]))
        return imports

    def symbol_counts_by_file(self) -> dict[str, int]:
        """Return the number of indexed symbols per file path."""
        rows = self.connection.execute(
            """
            SELECT file_path, COUNT(*) AS c
            FROM symbols
            GROUP BY file_path
            ORDER BY file_path
            """
        ).fetchall()
        return {str(row["file_path"]): int(row["c"]) for row in rows}

    def cross_file_reference_counts(self) -> list[tuple[str, str, int]]:
        """Count exact/scoped references between distinct files, grouped by file pair.

        Only syntax-proven and scope-narrowed references count — heuristic matches
        never establish cross-file structure. The target file comes from the resolved
        symbol row because reference relations never store a target file path.
        Ordered by (from path, to path) so the projection is deterministic.
        """
        rows = self.connection.execute(
            """
            SELECT r.from_file_path AS from_path, s.file_path AS to_path, COUNT(*) AS c
            FROM relations r
            JOIN symbols s ON s.id = r.to_symbol_id
            WHERE r.kind = ? AND r.resolution IN (?, ?) AND r.from_file_path != s.file_path
            GROUP BY r.from_file_path, s.file_path
            ORDER BY r.from_file_path, s.file_path
            """,
            [
                str(RelationKind.REFERENCES),
                str(ResolutionMethod.EXACT),
                str(ResolutionMethod.SCOPED),
            ],
        ).fetchall()
        return [(str(row["from_path"]), str(row["to_path"]), int(row["c"])) for row in rows]

    def cross_file_reference_sites(
        self, from_file_path: str, to_file_path: str, limit: int
    ) -> list[tuple[str, int]]:
        """Return example (from path, line) sites for one cross-file pair, exact first."""
        normalized_limit = min(10, max(1, limit))
        rows = self.connection.execute(
            """
            SELECT r.from_file_path AS from_path, r.start_line AS line
            FROM relations r
            JOIN symbols s ON s.id = r.to_symbol_id
            WHERE r.kind = ? AND r.resolution IN (?, ?)
                AND r.from_file_path = ? AND s.file_path = ?
                AND r.start_line IS NOT NULL
            ORDER BY CASE WHEN r.resolution = ? THEN 0 ELSE 1 END, r.start_line, r.id
            LIMIT ?
            """,
            [
                str(RelationKind.REFERENCES),
                str(ResolutionMethod.EXACT),
                str(ResolutionMethod.SCOPED),
                from_file_path,
                to_file_path,
                str(ResolutionMethod.EXACT),
                normalized_limit,
            ],
        ).fetchall()
        return [(str(row["from_path"]), int(row["line"])) for row in rows]

    def import_edges(self) -> list[tuple[str, str]]:
        """Return distinct (importing file, imported dotted name) pairs, ordered."""
        rows = self.connection.execute(
            """
            SELECT DISTINCT from_file_path, to_name
            FROM relations
            WHERE kind = ? AND to_name IS NOT NULL
            ORDER BY from_file_path, to_name
            """,
            [str(RelationKind.IMPORTS)],
        ).fetchall()
        return [(str(row["from_file_path"]), str(row["to_name"])) for row in rows]

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
            body_slice = read_symbol_source(
                Path(str(file_row["project_root"])), symbol, max_lines=max_body_lines
            )
            if body_slice is not None:
                body = body_slice.text
                body_truncated = body_slice.truncated
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
        relations, _ = self.unresolved_references_by_name(name)
        return relations

    def unresolved_references_by_name(
        self,
        name: str,
        *,
        limit: int | None = None,
    ) -> tuple[list[Relation], int]:
        """Return bounded incoming unresolved references for a name, plus the exact total.

        A common name can carry thousands of unresolved sites, so a caller that projects
        only a handful must bound the read instead of slicing it afterwards. The total
        stays exact regardless of the bound, so the omission can be reported precisely.
        """
        with self._connection() as connection:
            sql = """
                SELECT *
                FROM relations
                WHERE kind = ? AND to_name = ? AND to_symbol_id IS NULL
                ORDER BY from_file_path, id
            """
            params: list[object] = [str(RelationKind.REFERENCES), name]
            if limit is not None:
                sql += " LIMIT ?"
                params.append(max(0, limit))
            rows = connection.execute(sql, params).fetchall()
            relations = [_map_relation(row) for row in rows]
            if limit is None or len(relations) < limit:
                return relations, len(relations)
            total = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM relations
                    WHERE kind = ? AND to_name = ? AND to_symbol_id IS NULL
                    """,
                    (str(RelationKind.REFERENCES), name),
                ).fetchone()[0]
            )
        return relations, total

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

    def languages_by_path(self, paths: Sequence[str]) -> dict[str, str]:
        """Return the indexed language of each given file path; unknown paths are omitted.

        Relations carry no language of their own, so evidence-coverage reporting reads
        the language the indexer actually recorded rather than guessing from a suffix.
        """
        ordered_paths = sorted(set(paths))
        if not ordered_paths:
            return {}
        found: dict[str, str] = {}
        with self._connection() as connection:
            for batch in _sorted_batches(ordered_paths):
                placeholders = ", ".join("?" for _ in batch)
                rows = connection.execute(
                    f"SELECT path, language FROM files WHERE path IN ({placeholders})",
                    batch,
                ).fetchall()
                for row in rows:
                    found[str(row["path"])] = str(row["language"])
        return found

    def _languages_for_paths(self, paths: set[str]) -> list[str]:
        return sorted(set(self.languages_by_path(sorted(paths)).values()))

    def workspace_languages(self) -> list[str]:
        """Return every language present in the index, sorted."""
        return self._workspace_languages()

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
