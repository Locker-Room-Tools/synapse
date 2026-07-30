"""FastMCP tools for Synapse."""

from dataclasses import asdict
from pathlib import Path

from synapse.core.config import (
    ConfigScope,
    EffectiveConfig,
    config_file_path,
    load_default_ignored_directories,
    load_effective_config,
    load_project_config,
    load_user_config,
    normalize_ignore_entry,
    write_project_ignored_directories,
)
from synapse.core.context import ContextQuery, Direction, query_context
from synapse.core.index import SymbolIndex, relation_summary, symbol_summary
from synapse.core.indexing import index_workspace
from synapse.core.lifecycle import ensure_workspace, require_workspace_ready
from synapse.core.provenance import runtime_provenance
from synapse.core.watch.state import watch_status_payload
from synapse.core.workspace import db_path, require_workspace_path
from synapse.mcp.profiles import ToolProfile, tool
from synapse.mcp.workspace import current_workspace

_DIRECTORIES_ARGUMENT = "the directories argument"
_ACCEPTED_FORMS = (
    "bare directory name, matched at any depth (e.g. 'node_modules')",
    "root-anchored name, matched only at the workspace root (e.g. '/build')",
    "workspace-relative path, anchored at the workspace root (e.g. 'src/generated')",
)
_REJECTED_FORMS = (
    "absolute paths",
    "'.' or '..' segments",
    "glob patterns",
    "empty strings",
)


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


def _not_found(target: str) -> dict[str, object]:
    """Uniform not-found envelope: every query tool returns a dict, never None."""
    return {
        "found": False,
        "target": target,
        "reason": "not-indexed",
        "hint": (
            "Verify the name with synapse_get_definition or synapse_query_context; "
            "re-run synapse_ensure_workspace if the index may be stale."
        ),
    }


def _takes_effect(config: EffectiveConfig) -> str:
    return (
        f"next watch sweep (<= {config.watch.poll_interval_s}s); "
        "synapse_index_workspace applies it immediately"
    )


def _normalized_directories(directories: list[str]) -> tuple[list[str], dict[str, str]]:
    """Canonicalize every requested entry before any write, deduplicating in input order."""
    if not directories:
        msg = "directories must contain at least one entry."
        raise ValueError(msg)
    requested: list[str] = []
    normalized: dict[str, str] = {}
    for raw in directories:
        value = normalize_ignore_entry(raw, source=_DIRECTORIES_ARGUMENT)
        if value != raw:
            normalized[raw] = value
        if value not in requested:
            requested.append(value)
    return requested, normalized


def _mutation_payload(
    workspace_root: Path,
    normalized: dict[str, str],
    *,
    added: list[str],
    removed: list[str],
    already_present: list[str],
    already_covered_by_builtin: list[str],
    not_present: list[str],
) -> dict[str, object]:
    config = load_effective_config(workspace_root)
    project = load_project_config(workspace_root)
    return {
        "workspace_path": str(workspace_root),
        "scope": str(ConfigScope.PROJECT),
        "config_path": str(config.project_config_path),
        "added": added,
        "removed": removed,
        "already_present": already_present,
        "already_covered_by_builtin": already_covered_by_builtin,
        "not_present": not_present,
        "normalized": normalized,
        "project_ignored_directories": sorted(project.ignored_directories),
        "effective_ignored_directories": [entry.value for entry in config.ignored_directories],
        "takes_effect": _takes_effect(config),
    }


@tool(ToolProfile.DEFAULT)
def synapse_ensure_workspace(workspace_path: str = ".") -> dict[str, object]:
    """Initialize or repair Synapse before any code navigation or query.

    Idempotent: returns action (initialized/reused/repaired), daemon health, and index
    counts. Query tools reject uninitialized or degraded workspaces; re-call this when
    they do.
    """
    return ensure_workspace(_workspace_root(workspace_path)).to_payload()


@tool(ToolProfile.DEFAULT, structured_output=False)
def synapse_query_context(
    question: str,
    symbol_ids: list[str] | None = None,
    direction: str = "both",
    max_depth: int = 3,
    token_budget: int = 4000,
    include_source: bool = False,
    workspace_path: str = ".",
) -> str:
    """First call for architecture, lifecycle, impact, and multi-file flow questions.

    One bounded query: finds seeds (explicit symbol_ids, question matches, or a
    structural fallback when nothing matches), traverses stored relations up to
    max_depth (direction: in|out|both; only exact/scoped edges are transit —
    heuristic references stay leaf evidence), and returns one JSON bundle: ranked
    seeds, nodes and extra edges with file:line + resolution + confidence, flows
    ranked by aggregate path trust then question relevance, and explicit
    coverage/truncation. token_budget is a maximum, not a target: low-relevance
    evidence is omitted with explicit coverage accounting, and flows are omitted
    (coverage.projection.flows_omitted) when no chain is question-relevant.
    token_budget is an estimate at 4 chars/token; the returned string never exceeds
    token_budget*4 characters. Empty or truncated results are never proof of
    absence — check coverage. Follow up with at most a few targeted
    synapse_get_definition / synapse_find_references / synapse_get_symbol_context
    calls; do not re-run the same investigation through shell search.
    """
    try:
        parsed_direction = Direction(direction)
    except ValueError as exc:
        msg = f"direction must be one of: {', '.join(item.value for item in Direction)}."
        raise ValueError(msg) from exc
    workspace_root = require_workspace_ready(_workspace_root(workspace_path))
    context_query = ContextQuery(
        question=question,
        symbol_ids=tuple(symbol_ids or ()),
        direction=parsed_direction,
        max_depth=max_depth,
        token_budget=token_budget,
        include_source=include_source,
    )
    return query_context(
        SymbolIndex(db_path(workspace_root)),
        context_query,
        workspace_root=workspace_root,
    )


@tool()
def synapse_index_workspace(workspace_path: str = ".", force: bool = False) -> dict[str, object]:
    """Explicitly re-index a workspace; recovery and administration only.

    Not a navigation step: use synapse_ensure_workspace first. force=True rebuilds the
    index from scratch under the watch lock. Returns compact indexing stats.
    """
    workspace_root = require_workspace_ready(_workspace_root(workspace_path))
    return asdict(index_workspace(workspace_root, force=force))


@tool()
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


@tool(ToolProfile.DEFAULT)
def synapse_get_definition(
    symbol_id: str | None = None,
    name: str | None = None,
    workspace_path: str = ".",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    """Resolve a declaration to a stable symbol_id; prefer over opening files.

    Provide symbol_id OR exact name. Returns one symbol, {candidates, page} when the
    name is ambiguous, or {found: false, ...} when not indexed.
    """
    if symbol_id is None and name is None:
        msg = "Either symbol_id or name must be provided."
        raise ValueError(msg)
    index = _workspace_index(workspace_path)
    if symbol_id is not None:
        symbol = index.get_symbol(symbol_id)
        return symbol_summary(symbol) if symbol is not None else _not_found(symbol_id)
    assert name is not None
    candidates, page = index.get_definition_page(name, limit=limit, offset=offset)
    total = page["total"]
    assert isinstance(total, int)
    if total == 0:
        return _not_found(name)
    if total == 1:
        return symbol_summary(index.get_definition(name)[0])
    return {
        "candidates": [symbol_summary(candidate) for candidate in candidates],
        "page": page,
    }


@tool()
def synapse_get_file_outline(
    file_path: str,
    workspace_path: str = ".",
    max_symbols: int = 200,
) -> dict[str, object]:
    """Structural outline of one file; prefer before reading a whole file.

    Each item carries kind, name, signature, and line_range. file_path is
    workspace-relative (absolute paths inside the workspace are accepted).
    Returns {found: false, ...} when the file is not indexed.
    """
    workspace_root = _workspace_root(workspace_path)
    normalized_file_path = _normalize_file_path(file_path, workspace_root)
    outline = _workspace_index(workspace_root).get_file_outline(
        normalized_file_path,
        max_symbols=max_symbols,
    )
    return outline if outline is not None else _not_found(normalized_file_path)


@tool()
def synapse_workspace_stats(workspace_path: str = ".") -> dict[str, object]:
    """Return indexed workspace statistics (files, symbols, language mix).

    Also reports `runtime`: which Synapse build is serving this call and where it was
    loaded from, so a stale installed tool is distinguishable from a live checkout.
    """
    stats = _workspace_index(workspace_path).workspace_stats()
    stats["runtime"] = runtime_provenance().to_payload()
    return stats


@tool()
def synapse_watch_status(workspace_path: str = ".") -> dict[str, object]:
    """Read-only watch daemon freshness and health; diagnosis only, never repairs.

    Safe before initialization. Use synapse_ensure_workspace to repair.
    """
    return watch_status_payload(_workspace_root(workspace_path))


@tool()
def synapse_project_map(
    workspace_path: str = ".",
    limit: int = 50,
    offset: int = 0,
    top_symbols_limit: int = 20,
) -> dict[str, object]:
    """Return a compact paged map of the workspace structure and key symbols.

    Best first call for broad architecture questions. top_symbols contains type and
    function declarations only; namespace names are aggregated (deduplicated, with a
    total) under `namespaces`.
    """
    return _workspace_index(workspace_path).project_map(
        limit=limit,
        offset=offset,
        top_symbols_limit=top_symbols_limit,
    )


@tool()
def synapse_get_file_dependencies(
    file_path: str,
    workspace_path: str = ".",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    """Return file-level import dependencies for one indexed file (what it imports).

    file_path is workspace-relative (absolute paths inside the workspace are accepted).
    Returns {found: false, ...} when the file is not indexed.
    """
    workspace_root = _workspace_root(workspace_path)
    normalized_file_path = _normalize_file_path(file_path, workspace_root)
    dependencies = _workspace_index(workspace_root).get_file_dependencies(
        normalized_file_path,
        limit=limit,
        offset=offset,
    )
    return dependencies if dependencies is not None else _not_found(normalized_file_path)


@tool(ToolProfile.DEFAULT)
def synapse_get_symbol_context(
    symbol_id: str,
    include_body: bool = False,
    workspace_path: str = ".",
    children_limit: int = 50,
    children_offset: int = 0,
    max_body_lines: int = 200,
) -> dict[str, object]:
    """Structural context around one symbol: parent, paged children, optional body.

    symbol_id comes from synapse_query_context or synapse_get_definition. Set
    include_body=True to read the implementation source; prefer this over reading the
    file. body is capped at max_body_lines and body_truncated reports a cut — narrow
    to a child symbol or raise the cap for more. Returns {found: false, ...} for an
    unknown symbol_id.
    """
    context = _workspace_index(workspace_path).get_symbol_context(
        symbol_id,
        include_body=include_body,
        children_limit=children_limit,
        children_offset=children_offset,
        max_body_lines=max_body_lines,
    )
    return context if context is not None else _not_found(symbol_id)


@tool()
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


@tool(ToolProfile.DEFAULT)
def synapse_find_references(
    symbol_id: str | None = None,
    name: str | None = None,
    workspace_path: str = ".",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    """Find usages (incoming references); prefer over grep across the workspace.

    Provide symbol_id (preferred; from synapse_get_definition or
    synapse_search_symbols) OR name. Returns confirmed reference items (match:
    heuristic), same-name possible_items (match: ambiguous/unresolved with candidate
    symbol ids — never confirmed usages), per-item line/byte_column, affected files,
    a coverage block (extraction completeness, counts, limitations), and page
    metadata. Empty results mean no indexed references were found under partial
    coverage — not proof the symbol is unused.
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


@tool()
def synapse_related_symbols(
    symbol_id: str,
    limit: int = 20,
    workspace_path: str = ".",
    offset: int = 0,
) -> dict[str, object]:
    """Return graph neighbors of one symbol.

    Includes referenced symbols, referencing symbols, container or file siblings, and
    name-stem matches. Returns {found: false, ...} for an unknown symbol_id.
    """
    related = _workspace_index(workspace_path).related_symbols(
        symbol_id,
        limit=limit,
        offset=offset,
    )
    return related if related is not None else _not_found(symbol_id)


@tool()
def synapse_compact_context(
    symbol_id: str,
    workspace_path: str = ".",
) -> dict[str, object]:
    """Minimum context to understand a symbol; prefer over reading source.

    Returns a compact definition with capped dependency and related-name lists.
    Returns {found: false, ...} for an unknown symbol_id.
    """
    context = _workspace_index(workspace_path).compact_context(symbol_id)
    return context if context is not None else _not_found(symbol_id)


@tool()
def synapse_get_config(workspace_path: str = ".") -> dict[str, object]:
    """Read Synapse configuration: effective values, per-entry source, and write targets.

    Self-describing; no other documentation is needed to configure Synapse. Returns each
    option with its type, accepted input forms, current effective value, the source of every
    entry (built-in/global/project), the file writes land in, and when a change takes effect.
    Safe before initialization.
    """
    workspace_root = _workspace_root(workspace_path)
    config = load_effective_config(workspace_root)
    return {
        "workspace_path": str(workspace_root),
        "project_config_path": str(config.project_config_path),
        "project_config_exists": config.project_config_exists,
        "global_config_path": str(config.global_config_path),
        "watch_poll_interval_s": config.watch.poll_interval_s,
        "options": {
            "ignored_directories": {
                "type": "list[str]",
                "accepted_forms": list(_ACCEPTED_FORMS),
                "rejected": list(_REJECTED_FORMS),
                "case_sensitive": True,
                "add_with": "synapse_add_ignored_directories",
                "remove_with": "synapse_remove_ignored_directories",
                "writes_to": str(config.project_config_path),
                "layers": [str(scope) for scope in ConfigScope],
                "takes_effect": _takes_effect(config),
                "effective": [
                    {
                        "value": entry.value,
                        "sources": [str(scope) for scope in entry.sources],
                    }
                    for entry in config.ignored_directories
                ],
            },
        },
    }


@tool()
def synapse_add_ignored_directories(
    directories: list[str],
    workspace_path: str = ".",
) -> dict[str, object]:
    """Stop indexing directories; writes the project config, not the global one.

    Each entry is a bare directory name matched at any depth ("node_modules"), a
    root-anchored name ("/build"), or a workspace-relative path ("src/generated"). No globs,
    no absolute paths, no ".." segments. Built-in ignores are reported as already covered
    instead of being written. Any invalid entry rejects the whole call and writes nothing.
    Ignored files leave the index on the next watch sweep; call synapse_index_workspace to
    apply immediately.
    """
    workspace_root = _workspace_root(workspace_path)
    requested, normalized = _normalized_directories(directories)
    defaults = load_default_ignored_directories()
    entries = set(load_project_config(workspace_root).ignored_directories)

    added: list[str] = []
    already_present: list[str] = []
    already_covered_by_builtin: list[str] = []
    for value in requested:
        if value in defaults:
            already_covered_by_builtin.append(value)
        elif value in entries:
            already_present.append(value)
        else:
            entries.add(value)
            added.append(value)

    write_project_ignored_directories(workspace_root, entries)
    return _mutation_payload(
        workspace_root,
        normalized,
        added=added,
        removed=[],
        already_present=already_present,
        already_covered_by_builtin=already_covered_by_builtin,
        not_present=[],
    )


@tool()
def synapse_remove_ignored_directories(
    directories: list[str],
    workspace_path: str = ".",
) -> dict[str, object]:
    """Resume indexing directories; removes entries from the project config only.

    Built-in ignores and entries inherited from the global user config cannot be removed
    here and raise an error naming where they come from. Entries that are not ignored
    anywhere are reported in not_present and are not an error. Any invalid entry rejects the
    whole call and writes nothing. Restored files re-enter the index on the next watch
    sweep; call synapse_index_workspace to apply immediately.
    """
    workspace_root = _workspace_root(workspace_path)
    requested, normalized = _normalized_directories(directories)
    defaults = load_default_ignored_directories()
    entries = set(load_project_config(workspace_root).ignored_directories)
    global_entries = load_user_config().ignored_directories

    for value in requested:
        if value in defaults:
            msg = (
                f"Cannot remove built-in ignored directory {value!r}. "
                "Built-ins ship with Synapse and are not removable."
            )
            raise ValueError(msg)
        if value not in entries and value in global_entries:
            msg = (
                f"{value!r} is not in the project config; it is inherited from the global "
                f"config at {config_file_path()}. Remove it with: "
                f"synapse config ignored-dirs remove {value} --scope global"
            )
            raise ValueError(msg)

    removed: list[str] = []
    not_present: list[str] = []
    for value in requested:
        if value in entries:
            entries.discard(value)
            removed.append(value)
        else:
            not_present.append(value)

    write_project_ignored_directories(workspace_root, entries)
    return _mutation_payload(
        workspace_root,
        normalized,
        added=[],
        removed=removed,
        already_present=[],
        already_covered_by_builtin=[],
        not_present=not_present,
    )
