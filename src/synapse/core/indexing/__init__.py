"""Indexing pipeline: crawl, hash, parse, and reconcile references."""

from synapse.core.indexing.crawler import hash_file, hash_source, iter_source_files
from synapse.core.indexing.parser import (
    ParsedSource,
    build_reference_relations,
    build_relations,
    extract_references,
    parse_file,
    parse_source,
)
from synapse.core.indexing.pipeline import IndexStats, index_source_file, index_workspace
from synapse.core.indexing.references import reconcile_affected_references, symbol_names

__all__ = [
    "IndexStats",
    "ParsedSource",
    "build_reference_relations",
    "build_relations",
    "extract_references",
    "hash_file",
    "hash_source",
    "index_source_file",
    "index_workspace",
    "iter_source_files",
    "parse_file",
    "parse_source",
    "reconcile_affected_references",
    "symbol_names",
]
