"""FastMCP tools for Synapse."""

from dataclasses import asdict
from pathlib import Path

from synapse.core.index import SymbolIndex, relation_summary, symbol_summary
from synapse.core.indexing import index_workspace
from synapse.core.watch.state import watch_status_payload
from synapse.core.workspace import db_path, normalize_workspace_path
from synapse.mcp.server import mcp
from synapse.mcp.workspace import current_workspace


def _workspace_root(path: str | Path = ".") -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return normalize_workspace_path(candidate)
    return normalize_workspace_path(current_workspace() / candidate)


def _workspace_index(path: str | Path = ".") -> SymbolIndex:
    return SymbolIndex(db_path(_workspace_root(path)))


def _normalize_file_path(file_path: str, workspace_root: Path) -> str:
    candidate = Path(file_path)
    if candidate.is_absolute() and candidate.is_relative_to(workspace_root):
        return candidate.relative_to(workspace_root).as_posix()
    return candidate.as_posix()


@mcp.tool()
def synapse_index_workspace(workspace_path: str = ".", force: bool = False) -> dict[str, object]:
    """Index or re-index a workspace and return compact indexing stats."""
    return asdict(index_workspace(_workspace_root(workspace_path), force=force))


@mcp.tool()
def synapse_search_symbols(
    query: str,
    kind: str | None = None,
    language: str | None = None,
    limit: int = 20,
    workspace_path: str = ".",
) -> dict[str, object]:
    """Primary symbol lookup; prefer over grep/ripgrep; returns reusable symbol_id."""
    items = _workspace_index(workspace_path).search_symbols(
        query,
        kind=kind,
        language=language,
        limit=limit,
    )
    return {"items": [symbol_summary(item) for item in items]}


@mcp.tool()
def synapse_get_definition(
    symbol_id: str | None = None,
    name: str | None = None,
    workspace_path: str = ".",
) -> dict[str, object] | None:
    """Return a definition with stable symbol_id; prefer over opening files."""
    if symbol_id is None and name is None:
        msg = "Either symbol_id or name must be provided."
        raise ValueError(msg)
    index = _workspace_index(workspace_path)
    if symbol_id is not None:
        symbol = index.get_symbol(symbol_id)
        return symbol_summary(symbol) if symbol is not None else None
    assert name is not None
    candidates = index.get_definition(name)
    if not candidates:
        return None
    if len(candidates) == 1:
        return symbol_summary(candidates[0])
    return {"candidates": [symbol_summary(candidate) for candidate in candidates]}


@mcp.tool()
def synapse_get_file_outline(
    file_path: str,
    workspace_path: str = ".",
) -> dict[str, object] | None:
    """Structural outline; prefer before reading a whole file."""
    workspace_root = _workspace_root(workspace_path)
    normalized_file_path = _normalize_file_path(file_path, workspace_root)
    return _workspace_index(workspace_root).get_file_outline(normalized_file_path)


@mcp.tool()
def synapse_workspace_stats(workspace_path: str = ".") -> dict[str, object]:
    """Return indexed workspace statistics (files, symbols, language mix)."""
    return _workspace_index(workspace_path).workspace_stats()


@mcp.tool()
def synapse_watch_status(workspace_path: str = ".") -> dict[str, object]:
    """Return read-only watch daemon freshness and health status."""
    return watch_status_payload(_workspace_root(workspace_path))


@mcp.tool()
def synapse_project_map(workspace_path: str = ".") -> dict[str, object]:
    """Return a compact map of the workspace structure and key symbols."""
    return _workspace_index(workspace_path).project_map()


@mcp.tool()
def synapse_get_file_dependencies(
    file_path: str,
    workspace_path: str = ".",
) -> dict[str, object] | None:
    """Return file-level import dependencies for one indexed file."""
    workspace_root = _workspace_root(workspace_path)
    normalized_file_path = _normalize_file_path(file_path, workspace_root)
    return _workspace_index(workspace_root).get_file_dependencies(normalized_file_path)


@mcp.tool()
def synapse_get_symbol_context(
    symbol_id: str,
    include_body: bool = False,
    workspace_path: str = ".",
) -> dict[str, object] | None:
    """Return compact structural context around one symbol."""
    return _workspace_index(workspace_path).get_symbol_context(
        symbol_id,
        include_body=include_body,
    )


@mcp.tool()
def synapse_get_dependencies(
    symbol_id: str,
    workspace_path: str = ".",
) -> dict[str, object]:
    """Return outgoing relations (dependencies) for one indexed symbol."""
    relations = _workspace_index(workspace_path).get_dependencies(symbol_id)
    return {"items": [relation_summary(relation) for relation in relations]}


@mcp.tool()
def synapse_find_references(
    symbol_id: str | None = None,
    name: str | None = None,
    workspace_path: str = ".",
) -> dict[str, object]:
    """Find usages; prefer over grep across the workspace."""
    if symbol_id is None and name is None:
        msg = "Either symbol_id or name must be provided."
        raise ValueError(msg)
    return _workspace_index(workspace_path).find_references(symbol_id=symbol_id, name=name)


@mcp.tool()
def synapse_related_symbols(
    symbol_id: str,
    limit: int = 20,
    workspace_path: str = ".",
) -> dict[str, object] | None:
    """Return symbols related to a given symbol (graph-like neighbors)."""
    return _workspace_index(workspace_path).related_symbols(symbol_id, limit=limit)


@mcp.tool()
def synapse_compact_context(
    symbol_id: str,
    workspace_path: str = ".",
) -> dict[str, object] | None:
    """Minimum context to understand a symbol; prefer over reading source."""
    return _workspace_index(workspace_path).compact_context(symbol_id)
