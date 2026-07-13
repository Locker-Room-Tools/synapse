"""Single-writer incremental index worker for watch batches."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from synapse.core.crawler import hash_file
from synapse.core.index import SymbolIndex
from synapse.core.languages import detect_language
from synapse.core.models import RelationKind, SourceFile, Symbol
from synapse.core.parser import (
    RawReference,
    build_reference_relations,
    build_relations,
    extract_references,
    parse_file,
)
from synapse.core.watch.state import append_journal_complete, append_journal_intent, utc_now
from synapse.core.workspace import db_path, normalize_workspace_path, write_metadata


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

    def __init__(self, workspace_path: str | Path, *, max_reference_fixups: int = 256) -> None:
        self.root = normalize_workspace_path(workspace_path)
        self.index = SymbolIndex(db_path(self.root))
        self.max_reference_fixups = max(1, max_reference_fixups)

    def apply_batch(
        self,
        *,
        reindex_paths: Iterable[str],
        remove_paths: Iterable[str],
        batch_id: str | None = None,
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
        result = self._apply_transaction(reindex_paths=reindex, remove_paths=remove)
        append_journal_complete(self.root, batch_id=actual_batch_id)
        return result

    def _apply_transaction(
        self,
        *,
        reindex_paths: list[str],
        remove_paths: list[str],
    ) -> WatchBatchResult:
        indexed_files = 0
        skipped_files = 0
        removed_files = 0
        raw_references_by_file: dict[str, list[RawReference]] = {}
        affected_names: set[str] = set()

        with self.index.transaction() as connection:
            existing_files = {
                source_file.path: source_file
                for source_file in self.index.list_indexed_files(connection=connection)
            }
            for rel_path in sorted(set(remove_paths) | set(reindex_paths)):
                affected_names.update(
                    _symbol_names(self.index.list_symbols_for_file(rel_path, connection=connection))
                )

            removed_files += self.index.remove_files(remove_paths, connection=connection)
            for rel_path in reindex_paths:
                batch_update = self._reindex_file(
                    rel_path,
                    existing_files.get(rel_path),
                    connection=connection,
                )
                indexed_files += batch_update.indexed_files
                skipped_files += batch_update.skipped_files
                removed_files += batch_update.removed_files
                affected_names.update(batch_update.affected_names)
                if batch_update.raw_references is not None:
                    raw_references_by_file[rel_path] = batch_update.raw_references

            name_index = self.index.symbol_name_index(connection=connection)
            dependent_paths = self._dependent_file_paths(affected_names, connection=connection)
            dependent_paths.update(raw_references_by_file)
            current_files = {
                source_file.path: source_file
                for source_file in self.index.list_indexed_files(connection=connection)
            }
            for rel_path in sorted(dependent_paths)[: self.max_reference_fixups]:
                raw_references = raw_references_by_file.get(rel_path)
                if raw_references is None:
                    source_file = current_files.get(rel_path)
                    if source_file is None:
                        continue
                    symbols = self.index.list_symbols_for_file(rel_path, connection=connection)
                    raw_references = extract_references(
                        self.root / rel_path,
                        source_file.language,
                        symbols,
                    )
                self.index.add_relations_for_file(
                    rel_path,
                    build_reference_relations(raw_references, name_index),
                    connection=connection,
                )

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
    ) -> _FileUpdate:
        absolute_path = self.root / rel_path
        if not absolute_path.exists() or not absolute_path.is_file():
            removed = self.index.remove_files([rel_path], connection=connection)
            return _FileUpdate(0, 0, removed, [], None)

        language = detect_language(absolute_path)
        if language is None:
            removed = self.index.remove_files([rel_path], connection=connection)
            return _FileUpdate(0, 0, removed, [], None)

        content_hash = hash_file(absolute_path)
        if existing_file is not None and existing_file.content_hash == content_hash:
            return _FileUpdate(0, 1, 0, [], None)

        symbols = parse_file(absolute_path, language, workspace_root=self.root)
        self.index.upsert_file(
            SourceFile(
                id=rel_path,
                path=rel_path,
                language=language,
                project_root=str(self.root),
                content_hash=content_hash,
                indexed_at=utc_now(),
            ),
            connection=connection,
        )
        self.index.replace_symbols_for_file(
            rel_path,
            symbols,
            build_relations(symbols),
            connection=connection,
        )
        return _FileUpdate(
            1,
            0,
            0,
            sorted(_symbol_names(symbols)),
            extract_references(absolute_path, language, symbols),
        )

    def _dependent_file_paths(
        self,
        names: set[str],
        *,
        connection: sqlite3.Connection,
    ) -> set[str]:
        if not names:
            return set()
        ordered_names = sorted(names)
        placeholders = ", ".join("?" for _ in ordered_names)
        rows = connection.execute(
            f"""
            SELECT DISTINCT file_id
            FROM relations
            WHERE kind = ? AND to_name IN ({placeholders})
            ORDER BY file_id
            LIMIT ?
            """,
            [str(RelationKind.REFERENCES), *ordered_names, self.max_reference_fixups],
        ).fetchall()
        return {str(row["file_id"]) for row in rows}


@dataclass(frozen=True, slots=True)
class _FileUpdate:
    indexed_files: int
    skipped_files: int
    removed_files: int
    affected_names: list[str]
    raw_references: list[RawReference] | None


def _symbol_names(symbols: Iterable[Symbol]) -> set[str]:
    names: set[str] = set()
    for symbol in symbols:
        names.add(symbol.name)
        if symbol.qualified_name is not None:
            names.add(symbol.qualified_name)
    return names
