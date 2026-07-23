"""FastMCP tools for Synapse."""

from dataclasses import asdict
from pathlib import Path

from synapse.core.index import SymbolIndex, relation_summary, symbol_summary
from synapse.core.indexing import index_workspace
from synapse.core.lifecycle import ensure_workspace, require_workspace_ready
from synapse.core.watch.state import watch_status_payload
from synapse.core.workspace import db_path, require_workspace_path
from synapse.mcp.server import mcp
from synapse.mcp.workspace import current_workspace


def _workspace_root(path: str | Path = ".") -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return require_workspace_path(candidate)
    return require_workspace_path(current_workspace() / candidate)


def _workspace_index(path: str | Path = ".") -> SymbolIndex:
    root = require_workspace_ready(_workspace_root(path))
    return SymbolIndex(db_path(root))


def _normalize_file_path(file_path: str, workspace_root: Path) -> str:
    candidate = Path(file_path)
    if candidate.is_absolute() and candidate.is_relative_to(workspace_root):
        return candidate.relative_to(workspace_root).as_posix()
    return candidate.as_posix()


@mcp.tool()
def synapse_ensure_workspace(workspace_path: str = ".") -> dict[str, object]:
    """Initialize or repair Synapse before any code navigation or query.

    Idempotent: returns action (initialized/reused/repaired), daemon health, and index
    counts. Query tools reject uninitialized or degraded workspaces; re-call this when
    they do.
    """
    return ensure_workspace(_workspace_root(workspace_path)).to_payload()


@mcp.tool()
def synapse_index_workspace(workspace_path: str = ".", force: bool = False) -> dict[str, object]:
    """Explicitly re-index a workspace; recovery and administration only.

    Not a navigation step: use synapse_ensure_workspace first. force=True rebuilds the
    index from scratch under the watch lock. Returns compact indexing stats.
    """
    workspace_root = require_workspace_ready(_workspace_root(workspace_path))
    return asdict(index_workspace(workspace_root, force=force))


@mcp.tool()
def synapse_search_symbols(
    query: str,
    kind: str | None = None,
    language: str | None = None,
    limit: int = 20,
    workspace_path: str = ".",
    offset: int = 0,
) -> dict[str, object]:
    """Primary symbol lookup; prefer over grep/ripgrep; returns reusable symbol_id.

    Matches by name (prefix first, substring fallback; exact names rank highest).
    Optional kind filter: namespace, package, module, class, interface, struct, record,
    enum, type, function, method, constructor, property, field, variable, constant,
    import. Returns {items, page}.
    """
    items, page = _workspace_index(workspace_path).search_symbols_page(
        query,
        kind=kind,
        language=language,
        limit=limit,
        offset=offset,
    )
    return {"items": [symbol_summary(item) for item in items], "page": page}


@mcp.tool()
def synapse_get_definition(
    symbol_id: str | None = None,
    name: str | None = None,
    workspace_path: str = ".",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object] | None:
    """Resolve a declaration to a stable symbol_id; prefer over opening files.

    Provide symbol_id OR exact name. Returns one symbol, {candidates, page} when the
    name is ambiguous, or None when not found.
    """
    if symbol_id is None and name is None:
        msg = "Either symbol_id or name must be provided."
        raise ValueError(msg)
    index = _workspace_index(workspace_path)
    if symbol_id is not None:
        symbol = index.get_symbol(symbol_id)
        return symbol_summary(symbol) if symbol is not None else None
    assert name is not None
    candidates, page = index.get_definition_page(name, limit=limit, offset=offset)
    total = page["total"]
    assert isinstance(total, int)
    if total == 0:
        return None
    if total == 1:
        return symbol_summary(index.get_definition(name)[0])
    return {
        "candidates": [symbol_summary(candidate) for candidate in candidates],
        "page": page,
    }


@mcp.tool()
def synapse_get_file_outline(
    file_path: str,
    workspace_path: str = ".",
    max_symbols: int = 200,
) -> dict[str, object] | None:
    """Structural outline of one file; prefer before reading a whole file.

    file_path is workspace-relative (absolute paths inside the workspace are accepted).
    Returns None when the file is not indexed.
    """
    workspace_root = _workspace_root(workspace_path)
    normalized_file_path = _normalize_file_path(file_path, workspace_root)
    return _workspace_index(workspace_root).get_file_outline(
        normalized_file_path,
        max_symbols=max_symbols,
    )


@mcp.tool()
def synapse_workspace_stats(workspace_path: str = ".") -> dict[str, object]:
    """Return indexed workspace statistics (files, symbols, language mix)."""
    return _workspace_index(workspace_path).workspace_stats()


@mcp.tool()
def synapse_watch_status(workspace_path: str = ".") -> dict[str, object]:
    """Read-only watch daemon freshness and health; diagnosis only, never repairs.

    Safe before initialization. Use synapse_ensure_workspace to repair.
    """
    return watch_status_payload(_workspace_root(workspace_path))


@mcp.tool()
def synapse_project_map(
    workspace_path: str = ".",
    limit: int = 50,
    offset: int = 0,
    top_symbols_limit: int = 20,
) -> dict[str, object]:
    """Return a compact paged map of the workspace structure and key symbols.

    Best first call for broad architecture questions.
    """
    return _workspace_index(workspace_path).project_map(
        limit=limit,
        offset=offset,
        top_symbols_limit=top_symbols_limit,
    )


@mcp.tool()
def synapse_get_file_dependencies(
    file_path: str,
    workspace_path: str = ".",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object] | None:
    """Return file-level import dependencies for one indexed file (what it imports).

    file_path is workspace-relative (absolute paths inside the workspace are accepted).
    Returns None when the file is not indexed.
    """
    workspace_root = _workspace_root(workspace_path)
    normalized_file_path = _normalize_file_path(file_path, workspace_root)
    return _workspace_index(workspace_root).get_file_dependencies(
        normalized_file_path,
        limit=limit,
        offset=offset,
    )


@mcp.tool()
def synapse_get_symbol_context(
    symbol_id: str,
    include_body: bool = False,
    workspace_path: str = ".",
    children_limit: int = 50,
    children_offset: int = 0,
) -> dict[str, object] | None:
    """Structural context around one symbol: parent, paged children, optional body.

    symbol_id comes from synapse_search_symbols or synapse_get_definition. Set
    include_body=True only when implementation text is needed; for the smallest view
    use synapse_compact_context. Returns None for an unknown symbol_id.
    """
    return _workspace_index(workspace_path).get_symbol_context(
        symbol_id,
        include_body=include_body,
        children_limit=children_limit,
        children_offset=children_offset,
    )


@mcp.tool()
def synapse_get_dependencies(
    symbol_id: str,
    workspace_path: str = ".",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    """Return outgoing relations from one symbol (what it references or contains).

    For incoming usages use synapse_find_references. symbol_id comes from
    synapse_search_symbols or synapse_get_definition. Returns {items, page}.
    """
    relations, page = _workspace_index(workspace_path).get_dependencies_page(
        symbol_id,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [relation_summary(relation) for relation in relations],
        "page": page,
    }


@mcp.tool()
def synapse_find_references(
    symbol_id: str | None = None,
    name: str | None = None,
    workspace_path: str = ".",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    """Find usages (incoming references); prefer over grep across the workspace.

    Provide symbol_id (preferred; from synapse_get_definition or
    synapse_search_symbols) OR name. Returns reference items, affected files, and page
    metadata.
    """
    if symbol_id is None and name is None:
        msg = "Either symbol_id or name must be provided."
        raise ValueError(msg)
    return _workspace_index(workspace_path).find_references(
        symbol_id=symbol_id,
        name=name,
        limit=limit,
        offset=offset,
    )


@mcp.tool()
def synapse_related_symbols(
    symbol_id: str,
    limit: int = 20,
    workspace_path: str = ".",
    offset: int = 0,
) -> dict[str, object] | None:
    """Return graph neighbors of one symbol.

    Includes referenced symbols, referencing symbols, container or file siblings, and
    name-stem matches. Returns None for an unknown symbol_id.
    """
    return _workspace_index(workspace_path).related_symbols(
        symbol_id,
        limit=limit,
        offset=offset,
    )


@mcp.tool()
def synapse_compact_context(
    symbol_id: str,
    workspace_path: str = ".",
) -> dict[str, object] | None:
    """Minimum context to understand a symbol; prefer over reading source.

    Returns a compact definition with capped dependency and related-name lists.
    Returns None for an unknown symbol_id.
    """
    return _workspace_index(workspace_path).compact_context(symbol_id)
