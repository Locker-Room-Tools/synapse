"""Workspace reconciliation sweeps for watch mode."""

from dataclasses import dataclass
from pathlib import Path

from synapse.core.index import SymbolIndex
from synapse.core.indexing.crawler import hash_source, iter_source_files
from synapse.core.watch.debounce import WatchBatch
from synapse.core.watch.worker import WatchWorker
from synapse.core.workspace import db_path, require_workspace_path


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """Summary returned after a watch reconciliation sweep."""

    workspace_path: str
    indexed_files: int
    skipped_files: int
    removed_files: int
    total_files: int
    total_symbols: int
    languages: list[str]


def build_reconcile_batch(
    workspace_path: str | Path,
) -> tuple[WatchBatch, int, dict[str, tuple[bytes, str]]]:
    """Crawl the workspace and return changed/deleted paths plus skipped count."""
    root = require_workspace_path(workspace_path)
    index = SymbolIndex(db_path(root))
    existing_files = {source_file.path: source_file for source_file in index.list_indexed_files()}
    seen_paths: set[str] = set()
    reindex_paths: list[str] = []
    prepared_sources: dict[str, tuple[bytes, str]] = {}
    skipped_files = 0

    for source_path in iter_source_files(root):
        relative_path = source_path.relative_to(root).as_posix()
        seen_paths.add(relative_path)
        source_bytes = source_path.read_bytes()
        content_hash = hash_source(source_bytes)
        existing = existing_files.get(relative_path)
        if existing is not None and existing.content_hash == content_hash:
            skipped_files += 1
            continue
        reindex_paths.append(relative_path)
        prepared_sources[relative_path] = (source_bytes, content_hash)

    remove_paths = sorted(set(existing_files) - seen_paths)
    return (
        WatchBatch(reindex_paths=sorted(reindex_paths), remove_paths=remove_paths),
        skipped_files,
        prepared_sources,
    )


def reconcile_workspace(workspace_path: str | Path) -> ReconcileResult:
    """Apply an incremental filesystem-vs-index reconciliation sweep."""
    root = require_workspace_path(workspace_path)
    batch, skipped_files, prepared_sources = build_reconcile_batch(root)
    batch_result = WatchWorker(root).apply_batch(
        reindex_paths=batch.reindex_paths,
        remove_paths=batch.remove_paths,
        prepared_sources=prepared_sources,
    )
    index = SymbolIndex(db_path(root))
    current_files = index.list_indexed_files()
    languages = sorted({source_file.language for source_file in current_files})
    stats = index.workspace_stats()
    total_files = stats["files"]
    total_symbols = stats["symbols"]
    assert isinstance(total_files, int)
    assert isinstance(total_symbols, int)
    return ReconcileResult(
        workspace_path=str(root),
        indexed_files=batch_result.indexed_files,
        skipped_files=skipped_files + batch_result.skipped_files,
        removed_files=batch_result.removed_files,
        total_files=total_files,
        total_symbols=total_symbols,
        languages=languages,
    )
