"""Reference reconciliation shared by indexing and watch updates."""

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from synapse.core.index import SymbolIndex
from synapse.core.indexing.parser import RawReference, build_reference_relations, parse_source
from synapse.core.models import Symbol


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

    for relative_path in sorted(dependent_paths):
        source_file = current_files.get(relative_path)
        if source_file is None:
            continue
        raw_references = raw_references_by_file.get(relative_path)
        if raw_references is None:
            absolute_path = root / relative_path
            source_bytes = absolute_path.read_bytes()
            raw_references = parse_source(
                absolute_path,
                source_file.language,
                source_bytes,
                workspace_root=root,
            ).references
        index.add_relations_for_file(
            relative_path,
            build_reference_relations(raw_references, name_index),
            connection=connection,
        )
