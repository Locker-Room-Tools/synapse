"""Workspace indexing orchestration."""

import sqlite3
import warnings
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from synapse.core.crawler import hash_source, iter_source_files
from synapse.core.index import SymbolIndex
from synapse.core.index_schema import (
    atomic_replace_database,
    cleanup_database_files,
    temporary_database_path,
)
from synapse.core.languages import detect_language
from synapse.core.models import SourceFile
from synapse.core.parser import ParsedSource, RawReference, build_relations, parse_source
from synapse.core.reference_reconciliation import (
    reconcile_affected_references,
    symbol_names,
)
from synapse.core.workspace import db_path, require_workspace_path, write_metadata


@dataclass(frozen=True, slots=True)
class IndexStats:
    """Summary returned after indexing a workspace."""

    workspace_path: str
    indexed_files: int
    skipped_files: int
    removed_files: int
    failed_files: int
    total_files: int
    total_symbols: int
    languages: list[str]


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def index_source_file(
    index: SymbolIndex,
    connection: sqlite3.Connection,
    *,
    workspace_root: Path,
    relative_path: str,
    absolute_path: Path,
    language: str,
    source_bytes: bytes,
    content_hash: str,
    indexed_at: str,
) -> ParsedSource:
    """Parse one source file and upsert its file row, symbols, and relations."""
    parsed_source = parse_source(
        absolute_path,
        language,
        source_bytes,
        workspace_root=workspace_root,
    )
    index.upsert_file(
        SourceFile(
            id=relative_path,
            path=relative_path,
            language=language,
            project_root=str(workspace_root),
            content_hash=content_hash,
            indexed_at=indexed_at,
        ),
        connection=connection,
    )
    index.replace_symbols_for_file(
        relative_path,
        parsed_source.symbols,
        build_relations(parsed_source.symbols),
        connection=connection,
    )
    return parsed_source


def index_workspace(workspace_path: str | Path = ".", *, force: bool = False) -> IndexStats:
    """Index or re-index a workspace into the local SQLite cache."""
    root = require_workspace_path(workspace_path)
    if force:
        from synapse.core.watch.supervisor import WatchLock

        with WatchLock(root):
            return _index_workspace(root, force=True)
    return _index_workspace(root, force=False)


def _index_workspace(root: Path, *, force: bool) -> IndexStats:
    target_db_path = db_path(root)
    temporary_db_path = temporary_database_path(target_db_path) if force else None
    active_db_path = temporary_db_path or target_db_path
    seen_paths: set[str] = set()
    raw_references_by_file: dict[str, list[RawReference]] = {}
    affected_names: set[str] = set()
    indexed_files = 0
    skipped_files = 0
    failed_files = 0

    try:
        index = SymbolIndex(active_db_path)
        with index.transaction() as connection:
            existing_files = (
                {}
                if force
                else {
                    source_file.path: source_file
                    for source_file in index.list_indexed_files(connection=connection)
                }
            )
            for source_path in iter_source_files(root):
                language = detect_language(source_path)
                if language is None:
                    continue
                relative_path = source_path.relative_to(root).as_posix()
                try:
                    source_bytes = source_path.read_bytes()
                    content_hash = hash_source(source_bytes)
                    existing = existing_files.get(relative_path)
                    if not force and existing is not None and existing.content_hash == content_hash:
                        seen_paths.add(relative_path)
                        skipped_files += 1
                        continue
                    if existing is not None:
                        affected_names.update(
                            symbol_names(
                                index.list_symbols_for_file(
                                    relative_path,
                                    connection=connection,
                                )
                            )
                        )
                    parsed_source = index_source_file(
                        index,
                        connection,
                        workspace_root=root,
                        relative_path=relative_path,
                        absolute_path=source_path,
                        language=language,
                        source_bytes=source_bytes,
                        content_hash=content_hash,
                        indexed_at=_utc_now(),
                    )
                except OSError as exc:
                    warnings.warn(
                        f"Skipping unreadable file {relative_path}: {exc}",
                        stacklevel=2,
                    )
                    failed_files += 1
                    continue

                seen_paths.add(relative_path)
                raw_references_by_file[relative_path] = parsed_source.references
                affected_names.update(symbol_names(parsed_source.symbols))
                indexed_files += 1

            removed_paths = sorted(set(existing_files) - seen_paths)
            for removed_path in removed_paths:
                affected_names.update(
                    symbol_names(
                        index.list_symbols_for_file(
                            removed_path,
                            connection=connection,
                        )
                    )
                )
            removed_files = (
                0
                if force
                else index.remove_files(
                    removed_paths,
                    connection=connection,
                )
            )
            reconcile_affected_references(
                root,
                index,
                connection,
                affected_names=affected_names,
                raw_references_by_file=raw_references_by_file,
            )
            current_files = index.list_indexed_files(connection=connection)
            languages = sorted({source_file.language for source_file in current_files})
            workspace_stats = index.workspace_stats(connection=connection)
            total_files = workspace_stats["files"]
            total_symbols = workspace_stats["symbols"]
            assert isinstance(total_files, int)
            assert isinstance(total_symbols, int)

        if temporary_db_path is not None:
            atomic_replace_database(temporary_db_path, target_db_path)

        write_metadata(root, last_indexed_at=_utc_now(), languages=languages)
        return IndexStats(
            workspace_path=str(root),
            indexed_files=indexed_files,
            skipped_files=skipped_files,
            removed_files=removed_files,
            failed_files=failed_files,
            total_files=total_files,
            total_symbols=total_symbols,
            languages=languages,
        )
    except Exception:
        if temporary_db_path is not None:
            cleanup_database_files(temporary_db_path)
        raise


def index_workspace_payload(
    workspace_path: str | Path = ".",
    *,
    force: bool = False,
) -> dict[str, object]:
    """Return a JSON-serializable index summary."""
    return asdict(index_workspace(workspace_path, force=force))
