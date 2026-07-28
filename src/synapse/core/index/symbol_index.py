"""Public entry object bundling SQLite schema, write, and read operations."""

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from synapse.core.index.reads import ReadProjections
from synapse.core.index.schema import (
    connection_scope,
    create_connection,
    initialize_schema,
    transaction_scope,
)
from synapse.core.index.writes import (
    add_relations_for_file,
    remove_files,
    replace_symbols_for_file,
    upsert_file,
)
from synapse.core.models import Relation, SourceFile, Symbol, SymbolKind


class SymbolIndex:
    """Preserve the public index API while delegating focused SQLite operations."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with transaction_scope(self._connect()) as connection:
            initialize_schema(connection)

    def _connect(self) -> sqlite3.Connection:
        return create_connection(self.db_path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with connection_scope(self._connect()) as connection:
            yield connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Open one transactional connection for multiple index writes."""
        with transaction_scope(self._connect()) as connection:
            yield connection

    def upsert_file(
        self,
        source_file: SourceFile,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Insert or update a tracked source file record."""
        if connection is not None:
            upsert_file(connection, source_file)
            return
        with self.transaction() as owned_connection:
            upsert_file(owned_connection, source_file)

    def replace_symbols_for_file(
        self,
        file_id: str,
        symbols: Iterable[Symbol],
        relations: Iterable[Relation] = (),
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Replace all extracted symbols and relations for one indexed file."""
        if connection is not None:
            replace_symbols_for_file(connection, file_id, symbols, relations)
            return
        with self.transaction() as owned_connection:
            replace_symbols_for_file(owned_connection, file_id, symbols, relations)

    def add_relations_for_file(
        self,
        file_id: str,
        relations: Iterable[Relation],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Refresh reference relations for one file without replacing symbols."""
        if connection is not None:
            add_relations_for_file(connection, file_id, relations)
            return
        with self.transaction() as owned_connection:
            add_relations_for_file(owned_connection, file_id, relations)

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
        if connection is not None:
            return remove_files(connection, paths)
        with self.transaction() as owned_connection:
            return remove_files(owned_connection, paths)

    def upsert_symbols(self, symbols: Iterable[Symbol]) -> None:
        """Insert or update symbols for one already tracked file."""
        symbol_list = list(symbols)
        file_ids = {symbol.file_path for symbol in symbol_list}
        if len(file_ids) != 1:
            msg = "upsert_symbols requires symbols for exactly one file"
            raise ValueError(msg)
        self.replace_symbols_for_file(symbol_list[0].file_path, symbol_list)

    def list_indexed_files(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[SourceFile]:
        """Return all indexed files."""
        if connection is not None:
            return ReadProjections(connection).list_indexed_files()
        with self._connection() as owned_connection:
            return ReadProjections(owned_connection).list_indexed_files()

    def list_symbols_for_file(
        self,
        file_path: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[Symbol]:
        """Return all symbols currently indexed for one file."""
        if connection is not None:
            return ReadProjections(connection).list_symbols_for_file(file_path)
        with self._connection() as owned_connection:
            return ReadProjections(owned_connection).list_symbols_for_file(file_path)

    def search_symbols(
        self,
        query: str,
        *,
        kind: str | SymbolKind | None = None,
        language: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Symbol]:
        """Search symbols by name or qualified name."""
        with self._connection() as connection:
            return ReadProjections(connection).search_symbols(
                query,
                kind=kind,
                language=language,
                limit=limit,
                offset=offset,
            )

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
        with self._connection() as connection:
            return ReadProjections(connection).search_symbols_page(
                query,
                kind=kind,
                language=language,
                limit=limit,
                offset=offset,
            )

    def get_symbol(self, symbol_id: str) -> Symbol | None:
        """Return one symbol by its stable identifier."""
        with self._connection() as connection:
            return ReadProjections(connection).get_symbol(symbol_id)

    def get_definition(self, name: str) -> list[Symbol]:
        """Return symbol definition candidates for a name."""
        with self._connection() as connection:
            return ReadProjections(connection).get_definition(name)

    def get_definition_page(
        self,
        name: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Symbol], dict[str, object]]:
        """Return one page of definition candidates."""
        with self._connection() as connection:
            return ReadProjections(connection).get_definition_page(
                name,
                limit=limit,
                offset=offset,
            )

    def symbol_name_index(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, list[str]]:
        """Return a name-to-symbol-ids index for reference resolution."""
        if connection is not None:
            return ReadProjections(connection).symbol_name_index()
        with self._connection() as owned_connection:
            return ReadProjections(owned_connection).symbol_name_index()

    def reference_source_files(
        self,
        names: Iterable[str],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> set[str]:
        """Return every indexed file containing references to affected names."""
        if connection is not None:
            return ReadProjections(connection).reference_source_files(names)
        with self._connection() as owned_connection:
            return ReadProjections(owned_connection).reference_source_files(names)

    def workspace_stats(
        self,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, object]:
        """Return file/symbol counts and per-language percentages."""
        if connection is not None:
            return ReadProjections(connection).workspace_stats()
        with self._connection() as owned_connection:
            return ReadProjections(owned_connection).workspace_stats()

    def project_map(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        top_symbols_limit: int = 20,
    ) -> dict[str, object]:
        """Return a bounded workspace file tree plus key symbols."""
        with self._connection() as connection:
            return ReadProjections(connection).project_map(
                limit=limit,
                offset=offset,
                top_symbols_limit=top_symbols_limit,
            )

    def get_file_outline(
        self,
        file_path: str,
        *,
        max_symbols: int = 200,
    ) -> dict[str, object] | None:
        """Return a bounded nested structural outline for one file."""
        with self._connection() as connection:
            return ReadProjections(connection).get_file_outline(
                file_path,
                max_symbols=max_symbols,
            )

    def get_symbol_context(
        self,
        symbol_id: str,
        include_body: bool = False,
        *,
        children_limit: int = 50,
        children_offset: int = 0,
    ) -> dict[str, object] | None:
        """Return compact context around a symbol."""
        with self._connection() as connection:
            return ReadProjections(connection).get_symbol_context(
                symbol_id,
                include_body=include_body,
                children_limit=children_limit,
                children_offset=children_offset,
            )

    def get_dependencies(self, symbol_id: str) -> list[Relation]:
        """Return outgoing relations for a symbol."""
        with self._connection() as connection:
            return ReadProjections(connection).get_dependencies(symbol_id)

    def get_dependencies_page(
        self,
        symbol_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Relation], dict[str, object]]:
        """Return one page of outgoing relations."""
        with self._connection() as connection:
            return ReadProjections(connection).get_dependencies_page(
                symbol_id,
                limit=limit,
                offset=offset,
            )

    def get_references(self, symbol_id: str) -> list[Relation]:
        """Return incoming resolved references for a symbol."""
        with self._connection() as connection:
            return ReadProjections(connection).get_references(symbol_id)

    def get_references_by_name(self, name: str) -> list[Relation]:
        """Return incoming unresolved references for a symbol name."""
        with self._connection() as connection:
            return ReadProjections(connection).get_references_by_name(name)

    def get_file_dependencies(
        self,
        file_path: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, object] | None:
        """Return one page of imports declared in an indexed file."""
        with self._connection() as connection:
            return ReadProjections(connection).get_file_dependencies(
                file_path,
                limit=limit,
                offset=offset,
            )

    def find_references(
        self,
        *,
        symbol_id: str | None = None,
        name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, object]:
        """Return one page of locations that reference a symbol."""
        with self._connection() as connection:
            return ReadProjections(connection).find_references(
                symbol_id=symbol_id,
                name=name,
                limit=limit,
                offset=offset,
            )

    def related_symbols(
        self,
        symbol_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, object] | None:
        """Return one page of structurally and semantically related symbols."""
        with self._connection() as connection:
            return ReadProjections(connection).related_symbols(
                symbol_id,
                limit=limit,
                offset=offset,
            )

    def compact_context(self, symbol_id: str) -> dict[str, object] | None:
        """Return minimal context with fixed dependency and related-name caps."""
        with self._connection() as connection:
            return ReadProjections(connection).compact_context(symbol_id)
