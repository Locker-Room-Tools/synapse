"""Reference reconciliation shared by indexing and watch updates."""

import hashlib
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from importlib.resources.abc import Traversable
from pathlib import Path
from urllib.parse import quote

from synapse.core.index import SCHEMA_VERSION, SymbolIndex
from synapse.core.indexing.parser import (
    REFERENCE_EXTRACTOR_VERSION,
    FileScope,
    RawReference,
    build_reference_relations,
    parse_source,
)
from synapse.core.indexing.resolution import ResolutionFacts, build_resolution_facts
from synapse.core.languages import name_separator
from synapse.core.languages.queries import QUERY_ROOT
from synapse.core.models import Symbol
from synapse.core.workspace import db_path

REFERENCE_FINGERPRINT_KEY = "reference_fingerprint"


def _iter_query_files(node: Traversable, prefix: str = "") -> Iterator[tuple[str, bytes]]:
    for child in sorted(node.iterdir(), key=lambda item: item.name):
        name = f"{prefix}{child.name}"
        if child.is_dir():
            yield from _iter_query_files(child, f"{name}/")
        elif child.name.endswith(".scm"):
            yield name, child.read_bytes()


def reference_extraction_fingerprint() -> str:
    """Return a digest of everything that shapes persisted reference relations.

    Covers all packaged queries (symbol queries affect enclosing-symbol attribution
    and name resolution, not just reference captures), the Python-side extractor
    version, and the SQLite schema version.
    """
    digest = hashlib.sha256()
    digest.update(f"schema:{SCHEMA_VERSION}\n".encode())
    digest.update(f"extractor:{REFERENCE_EXTRACTOR_VERSION}\n".encode())
    for name, content in _iter_query_files(QUERY_ROOT):
        digest.update(f"query:{name}:{len(content)}\n".encode())
        digest.update(content)
    return digest.hexdigest()


def reference_index_is_stale(root: Path) -> bool:
    """Return whether persisted relations predate the current extraction semantics.

    Probes the index read-only: this must never create, initialize, or migrate the
    database, because it runs while a watch daemon may still own the workspace.
    """
    database_path = db_path(root)
    if not database_path.exists():
        return False
    try:
        connection = sqlite3.connect(f"file:{quote(str(database_path))}?mode=ro", uri=True)
    except sqlite3.Error:
        return True
    try:
        row = connection.execute(
            "SELECT value FROM index_meta WHERE key = ?",
            (REFERENCE_FINGERPRINT_KEY,),
        ).fetchone()
    except sqlite3.Error:
        # No index_meta table: the index predates fingerprinting entirely.
        return True
    finally:
        connection.close()
    return row is None or str(row[0]) != reference_extraction_fingerprint()


def symbol_names(symbols: Iterable[Symbol]) -> set[str]:
    """Return simple and qualified names that can affect reference resolution."""
    names: set[str] = set()
    for symbol in symbols:
        names.add(symbol.name)
        if symbol.qualified_name is not None:
            names.add(symbol.qualified_name)
    return names


def reconcile_affected_references(
    root: Path,
    index: SymbolIndex,
    connection: sqlite3.Connection,
    *,
    affected_names: Iterable[str],
    raw_references_by_file: Mapping[str, Sequence[RawReference]],
    scopes_by_file: Mapping[str, FileScope] | None = None,
) -> None:
    """Rebuild changed and dependent references against the final symbol set."""
    dependent_paths = index.reference_source_files(
        affected_names,
        connection=connection,
    )
    dependent_paths.update(raw_references_by_file)
    current_files = {
        source_file.path: source_file
        for source_file in index.list_indexed_files(connection=connection)
    }
    name_index = index.symbol_name_index(connection=connection)
    kinds, qualified_names = index.symbol_resolution_facts(connection=connection)
    known_scopes = scopes_by_file or {}
    # One facts table per language separator, shared by every file in the batch.
    facts_by_separator: dict[str, ResolutionFacts] = {}

    for relative_path in sorted(dependent_paths):
        source_file = current_files.get(relative_path)
        if source_file is None:
            continue
        raw_references = raw_references_by_file.get(relative_path)
        scope = known_scopes.get(relative_path)
        if raw_references is None or scope is None:
            absolute_path = root / relative_path
            source_bytes = absolute_path.read_bytes()
            parsed = parse_source(
                absolute_path,
                source_file.language,
                source_bytes,
                workspace_root=root,
            )
            raw_references = parsed.references
            scope = parsed.scope
        separator = name_separator(source_file.language)
        facts = facts_by_separator.get(separator)
        if facts is None:
            facts = build_resolution_facts(
                kinds=kinds,
                qualified_names=qualified_names,
                separator=separator,
            )
            facts_by_separator[separator] = facts
        index.add_relations_for_file(
            relative_path,
            build_reference_relations(
                raw_references,
                name_index,
                facts=facts,
                scope=scope,
            ),
            connection=connection,
        )
