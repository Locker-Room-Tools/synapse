"""Workspace indexing orchestration."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from synapse.core.crawler import hash_file, iter_source_files
from synapse.core.index import SymbolIndex
from synapse.core.languages import detect_language
from synapse.core.models import SourceFile
from synapse.core.parser import (
    _RawReference,
    build_reference_relations,
    build_relations,
    extract_references,
    parse_file,
)
from synapse.core.workspace import db_path, normalize_workspace_path, write_metadata


@dataclass(frozen=True, slots=True)
class IndexStats:
    """Summary returned after indexing a workspace."""

    workspace_path: str
    indexed_files: int
    skipped_files: int
    removed_files: int
    total_files: int
    total_symbols: int
    languages: list[str]


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _temporary_db_path(target_db_path: Path) -> Path:
    target_db_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        prefix=f".{target_db_path.stem}.",
        suffix=".tmp",
        dir=target_db_path.parent,
        delete=False,
    ) as temporary_file:
        return Path(temporary_file.name)


def _remove_sqlite_sidecars(db_file: Path) -> None:
    for suffix in ("-journal", "-shm", "-wal"):
        db_file.with_name(f"{db_file.name}{suffix}").unlink(missing_ok=True)


def _remove_sqlite_file_set(db_file: Path) -> None:
    db_file.unlink(missing_ok=True)
    _remove_sqlite_sidecars(db_file)


def index_workspace(workspace_path: str | Path = ".", *, force: bool = False) -> IndexStats:
    """Index or re-index a workspace into the local SQLite cache."""
    root = normalize_workspace_path(workspace_path)
    target_db_path = db_path(root)
    temporary_db_path = _temporary_db_path(target_db_path) if force else None
    active_db_path = temporary_db_path or target_db_path
    index = SymbolIndex(active_db_path)
    seen_paths: set[str] = set()
    raw_references_by_file: dict[str, list[_RawReference]] = {}
    indexed_files = 0
    skipped_files = 0

    try:
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
                seen_paths.add(relative_path)
                content_hash = hash_file(source_path)
                existing = existing_files.get(relative_path)
                if not force and existing is not None and existing.content_hash == content_hash:
                    skipped_files += 1
                    continue

                indexed_at = _utc_now()
                index.upsert_file(
                    SourceFile(
                        id=relative_path,
                        path=relative_path,
                        language=language,
                        project_root=str(root),
                        content_hash=content_hash,
                        indexed_at=indexed_at,
                    ),
                    connection=connection,
                )
                symbols = parse_file(source_path, language, workspace_root=root)
                raw_references_by_file[relative_path] = extract_references(
                    source_path,
                    language,
                    symbols,
                )
                relations = build_relations(symbols)
                index.replace_symbols_for_file(
                    relative_path,
                    symbols,
                    relations,
                    connection=connection,
                )
                indexed_files += 1

            removed_paths = sorted(set(existing_files) - seen_paths)
            removed_files = (
                0
                if force
                else index.remove_files(
                    removed_paths,
                    connection=connection,
                )
            )
            name_index = index.symbol_name_index(connection=connection)
            for file_id, raw_references in raw_references_by_file.items():
                index.add_relations_for_file(
                    file_id,
                    build_reference_relations(raw_references, name_index),
                    connection=connection,
                )
            current_files = index.list_indexed_files(connection=connection)
            languages = sorted({source_file.language for source_file in current_files})
            workspace_stats = index.workspace_stats(connection=connection)
            total_files = workspace_stats["files"]
            total_symbols = workspace_stats["symbols"]
            assert isinstance(total_files, int)
            assert isinstance(total_symbols, int)

        if temporary_db_path is not None:
            _remove_sqlite_sidecars(target_db_path)
            temporary_db_path.replace(target_db_path)
            _remove_sqlite_sidecars(target_db_path)

        write_metadata(root, last_indexed_at=_utc_now(), languages=languages)
        return IndexStats(
            workspace_path=str(root),
            indexed_files=indexed_files,
            skipped_files=skipped_files,
            removed_files=removed_files,
            total_files=total_files,
            total_symbols=total_symbols,
            languages=languages,
        )
    except Exception:
        if temporary_db_path is not None:
            _remove_sqlite_file_set(temporary_db_path)
        raise


def index_workspace_payload(
    workspace_path: str | Path = ".",
    *,
    force: bool = False,
) -> dict[str, object]:
    """Return a JSON-serializable index summary."""
    return asdict(index_workspace(workspace_path, force=force))
