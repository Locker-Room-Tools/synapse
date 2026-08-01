"""SQLite-backed symbol index: schema, writes, read projections, and the repo map."""

from synapse.core.index.handles import is_symbol_handle, symbol_handle
from synapse.core.index.reads import (
    TOP_SYMBOL_KINDS,
    ReadProjections,
    relation_summary,
    symbol_summary,
)
from synapse.core.index.repo_map import (
    REPO_MAP_DERIVATION_VERSION,
    AreaResolver,
    MapAnchor,
    MapArea,
    MapBridge,
    MapEntrypoint,
    RepoMap,
    area_is_demoted_evidence,
    area_resolver,
    compute_repo_map,
    import_matches_file,
    import_segments,
    is_generated_path,
    is_test_path,
    kind_rank,
    load_repo_map,
    path_segments,
    refresh_repo_map,
)
from synapse.core.index.schema import SCHEMA, SCHEMA_VERSION
from synapse.core.index.source import SourceSlice, read_symbol_source
from synapse.core.index.symbol_index import SymbolIndex
from synapse.core.workspace import DEFAULT_DB_NAME

__all__ = [
    "DEFAULT_DB_NAME",
    "REPO_MAP_DERIVATION_VERSION",
    "AreaResolver",
    "SCHEMA",
    "SCHEMA_VERSION",
    "TOP_SYMBOL_KINDS",
    "MapAnchor",
    "MapArea",
    "MapBridge",
    "MapEntrypoint",
    "ReadProjections",
    "RepoMap",
    "SourceSlice",
    "SymbolIndex",
    "compute_repo_map",
    "is_symbol_handle",
    "read_symbol_source",
    "symbol_handle",
    "import_matches_file",
    "import_segments",
    "is_generated_path",
    "is_test_path",
    "area_is_demoted_evidence",
    "area_resolver",
    "kind_rank",
    "load_repo_map",
    "path_segments",
    "refresh_repo_map",
    "relation_summary",
    "symbol_summary",
]
