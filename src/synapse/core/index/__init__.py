"""SQLite-backed symbol index: schema, writes, read projections, and the repo map."""

from synapse.core.index.contract import INDEX_WRITER_CONTRACT_VERSION
from synapse.core.index.handles import HANDLE_LENGTH, HANDLE_PREFIX, is_symbol_handle, symbol_handle
from synapse.core.index.integrity import (
    IndexIntegrityError,
    handle_completeness_reason,
    repair_symbol_handles,
)
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
from synapse.core.index.source import (
    SourceSlice,
    VerifiedWindow,
    hash_source,
    read_verified_source_window,
)
from synapse.core.index.symbol_index import SymbolIndex
from synapse.core.workspace import DEFAULT_DB_NAME

__all__ = [
    "DEFAULT_DB_NAME",
    "HANDLE_LENGTH",
    "HANDLE_PREFIX",
    "INDEX_WRITER_CONTRACT_VERSION",
    "REPO_MAP_DERIVATION_VERSION",
    "AreaResolver",
    "IndexIntegrityError",
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
    "VerifiedWindow",
    "compute_repo_map",
    "handle_completeness_reason",
    "hash_source",
    "repair_symbol_handles",
    "is_symbol_handle",
    "read_verified_source_window",
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
