"""Single-writer incremental index worker for watch batches."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from synapse.core.index import SymbolIndex, refresh_repo_map
from synapse.core.indexing import index_source_file
from synapse.core.indexing.crawler import hash_source
from synapse.core.indexing.parser import FileScope, RawReference
from synapse.core.indexing.references import (
    reconcile_affected_references,
    symbol_names,
)
from synapse.core.languages import detect_language
from synapse.core.models import SourceFile
from synapse.core.watch.state import append_journal_complete, append_journal_intent, utc_now
from synapse.core.workspace import db_path, require_workspace_path, write_metadata


@dataclass(frozen=True, slots=True)
class WatchBatchResult:
    """Summary of one applied watch batch."""

    indexed_files: int
    skipped_files: int
    removed_files: int
    failed_files: int
    affected_names: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class WatchWorker:
    """Apply file-grained watch batches through one SQLite writer."""

    def __init__(self, workspace_path: str | Path) -> None:
        self.root = require_workspace_path(workspace_path)
        self.index = SymbolIndex(db_path(self.root))

    def apply_batch(
        self,
        *,
        reindex_paths: Iterable[str],
        remove_paths: Iterable[str],
        batch_id: str | None = None,
        prepared_sources: Mapping[str, tuple[bytes, str]] | None = None,
    ) -> WatchBatchResult:
        """Apply reindex/remove work inside one transaction."""
        reindex = sorted(set(reindex_paths))
        remove = sorted(set(remove_paths) - set(reindex))
        if not reindex and not remove:
            return WatchBatchResult(0, 0, 0, 0)

        actual_batch_id = batch_id or uuid4().hex
        append_journal_intent(
            self.root,
            batch_id=actual_batch_id,
            reindex_paths=reindex,
            remove_paths=remove,
        )
        result = self._apply_transaction(
            reindex_paths=reindex,
            remove_paths=remove,
            prepared_sources=prepared_sources or {},
        )
        append_journal_complete(self.root, batch_id=actual_batch_id)
        return result

    def _apply_transaction(
        self,
        *,
        reindex_paths: list[str],
        remove_paths: list[str],
        prepared_sources: Mapping[str, tuple[bytes, str]],
    ) -> WatchBatchResult:
        indexed_files = 0
        skipped_files = 0
        removed_files = 0
        raw_references_by_file: dict[str, list[RawReference]] = {}
        scopes_by_file: dict[str, FileScope] = {}
        affected_names: set[str] = set()

        with self.index.transaction() as connection:
            existing_files = {
                source_file.path: source_file
                for source_file in self.index.list_indexed_files(connection=connection)
            }
            for rel_path in sorted(set(remove_paths) | set(reindex_paths)):
                affected_names.update(
                    symbol_names(self.index.list_symbols_for_file(rel_path, connection=connection))
                )

            removed_files += self.index.remove_files(remove_paths, connection=connection)
            for rel_path in reindex_paths:
                batch_update = self._reindex_file(
                    rel_path,
                    existing_files.get(rel_path),
                    connection=connection,
                    prepared_source=prepared_sources.get(rel_path),
                )
                indexed_files += batch_update.indexed_files
                skipped_files += batch_update.skipped_files
                removed_files += batch_update.removed_files
                affected_names.update(batch_update.affected_names)
                if batch_update.raw_references is not None:
                    raw_references_by_file[rel_path] = batch_update.raw_references
                if batch_update.scope is not None:
                    scopes_by_file[rel_path] = batch_update.scope

            reconcile_affected_references(
                self.root,
                self.index,
                connection,
                affected_names=affected_names,
                raw_references_by_file=raw_references_by_file,
                scopes_by_file=scopes_by_file,
            )
            refresh_repo_map(connection)

            current_files_list = self.index.list_indexed_files(connection=connection)
            languages = sorted({source_file.language for source_file in current_files_list})
        write_metadata(self.root, last_indexed_at=utc_now(), languages=languages)
        return WatchBatchResult(
            indexed_files=indexed_files,
            skipped_files=skipped_files,
            removed_files=removed_files,
            failed_files=0,
            affected_names=sorted(affected_names),
        )

    def _reindex_file(
        self,
        rel_path: str,
        existing_file: SourceFile | None,
        *,
        connection: sqlite3.Connection,
        prepared_source: tuple[bytes, str] | None,
    ) -> _FileUpdate:
        absolute_path = self.root / rel_path
        if not absolute_path.exists() or not absolute_path.is_file():
            removed = self.index.remove_files([rel_path], connection=connection)
            return _FileUpdate(0, 0, removed, [], None)

        language = detect_language(absolute_path)
        if language is None:
            removed = self.index.remove_files([rel_path], connection=connection)
            return _FileUpdate(0, 0, removed, [], None)

        if prepared_source is None:
            source_bytes = absolute_path.read_bytes()
            content_hash = hash_source(source_bytes)
        else:
            source_bytes, content_hash = prepared_source
        if existing_file is not None and existing_file.content_hash == content_hash:
            return _FileUpdate(0, 1, 0, [], None)

        parsed_source = index_source_file(
            self.index,
            connection,
            workspace_root=self.root,
            relative_path=rel_path,
            absolute_path=absolute_path,
            language=language,
            source_bytes=source_bytes,
            content_hash=content_hash,
            indexed_at=utc_now(),
        )
        return _FileUpdate(
            1,
            0,
            0,
            sorted(symbol_names(parsed_source.symbols)),
            parsed_source.references,
            parsed_source.scope,
        )


@dataclass(frozen=True, slots=True)
class _FileUpdate:
    indexed_files: int
    skipped_files: int
    removed_files: int
    affected_names: list[str]
    raw_references: list[RawReference] | None
    scope: FileScope | None = None
