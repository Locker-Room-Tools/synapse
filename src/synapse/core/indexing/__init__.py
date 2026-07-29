"""Indexing pipeline: crawl, hash, parse, and reconcile references."""

from synapse.core.indexing.crawler import hash_file, hash_source, iter_source_files
from synapse.core.indexing.parser import (
    FileScope,
    ParsedSource,
    TypeBinding,
    build_reference_relations,
    build_relations,
    extract_references,
    parse_file,
    parse_source,
)
from synapse.core.indexing.pipeline import IndexStats, index_source_file, index_workspace
from synapse.core.indexing.references import (
    REFERENCE_FINGERPRINT_KEY,
    reconcile_affected_references,
    reference_extraction_fingerprint,
    reference_index_is_stale,
    symbol_names,
)
from synapse.core.indexing.resolution import (
    ResolutionFacts,
    ResolvedReference,
    build_resolution_facts,
    resolve_reference,
)

__all__ = [
    "REFERENCE_FINGERPRINT_KEY",
    "FileScope",
    "IndexStats",
    "ParsedSource",
    "ResolutionFacts",
    "ResolvedReference",
    "TypeBinding",
    "build_reference_relations",
    "build_resolution_facts",
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
    "reference_extraction_fingerprint",
    "reference_index_is_stale",
    "resolve_reference",
    "symbol_names",
]
