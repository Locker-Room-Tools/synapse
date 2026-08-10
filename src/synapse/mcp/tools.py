"""FastMCP tools for Synapse."""

from dataclasses import asdict
from pathlib import Path

from synapse.core.config import (
    ConfigScope,
    EffectiveConfig,
    IgnoreWriteResult,
    add_ignore_patterns,
    load_effective_config,
    remove_ignore_patterns,
    validate_ignore_pattern,
)
from synapse.core.index import SymbolIndex, relation_summary, symbol_summary
from synapse.core.indexing import index_workspace
from synapse.core.lifecycle import (
    ensure_navigation_ready,
    ensure_workspace,
    require_workspace_ready,
)
from synapse.core.navigation import (
    InspectRequest,
    OrientRequest,
    inspect_symbols,
    orient_workspace,
)
from synapse.core.provenance import runtime_provenance
from synapse.core.watch.state import watch_status_payload
from synapse.core.workspace import db_path, require_workspace_path
from synapse.mcp.profiles import ToolProfile, tool
from synapse.mcp.workspace import current_workspace

_DIRECTORIES_ARGUMENT = "the directories argument"
_ACCEPTED_FORMS = (
    "bare name, matched at any depth (e.g. 'node_modules')",
    "trailing slash to match directories only (e.g. 'build/')",
    "leading slash to anchor at the workspace root (e.g. '/dist')",
    "workspace-relative path, anchored at the workspace root (e.g. 'src/generated/')",
    "glob (e.g. '*.min.js', 'test_?.py', '[Bb]uild/', 'docs/**')",
    "leading '!' to re-include a path an earlier rule ignored (e.g. '!src/vendor/keep.js')",
)
_REJECTED_FORMS = (
    "absolute paths",
    "'.' or '..' segments",
    "empty strings",
)
_MAX_REPORTED_RULES = 200
_RULES_COVERAGE = (
    "This is the rule list, not the set of ignored paths. Whether a path is ignored depends on "
    "rule order, so no flat effective set exists."
)


def _workspace_root(path: str | Path = ".") -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return require_workspace_path(candidate)
    return require_workspace_path(current_workspace() / candidate)


def _workspace_index(path: str | Path = ".") -> SymbolIndex:
    root = require_workspace_ready(_workspace_root(path))
    return SymbolIndex(db_path(root))


def _navigation_workspace(path: str | Path = ".") -> Path:
    """Lazy readiness for the navigation tools; the whole decision lives in core."""
    return ensure_navigation_ready(_workspace_root(path))


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
            "Verify the name with synapse_orient; "
            "re-index with synapse_index_workspace if the index may be stale."
        ),
    }


def _takes_effect(config: EffectiveConfig) -> str:
    return (
        f"next watch sweep (<= {config.watch.poll_interval_s}s); "
        "synapse_index_workspace applies it immediately"
    )


def _normalized_directories(directories: list[str]) -> tuple[list[str], dict[str, str]]:
    """Validate every requested pattern before any write, deduplicating in input order."""
    if not directories:
        msg = "directories must contain at least one entry."
        raise ValueError(msg)
    requested: list[str] = []
    normalized: dict[str, str] = {}
    for raw in directories:
        value = validate_ignore_pattern(raw, source=_DIRECTORIES_ARGUMENT)
        if value != raw:
            normalized[raw] = value
        if value not in requested:
            requested.append(value)
    return requested, normalized


def _mutation_payload(
    workspace_root: Path,
    normalized: dict[str, str],
    result: IgnoreWriteResult,
) -> dict[str, object]:
    config = load_effective_config(workspace_root)
    project_layer = config.layer(ConfigScope.PROJECT)
    return {
        "workspace_path": str(workspace_root),
        "scope": str(result.scope),
        "config_path": str(result.path),
        "created": result.created,
        "added": list(result.added),
        "removed": list(result.removed),
        "negated": list(result.negated),
        "already_present": list(result.already_present),
        "not_present": list(result.not_present),
        "migrated_from_json": list(result.migrated_from_json),
        "normalized": normalized,
        "project_rules": [rule.pattern for rule in project_layer.rules],
        "takes_effect": _takes_effect(config),
        "coverage": _RULES_COVERAGE,
    }


@tool()
def synapse_ensure_workspace(workspace_path: str = ".") -> dict[str, object]:
    """Initialize or repair Synapse explicitly; navigation tools do this lazily.

    Idempotent: returns action (initialized/reused/repaired), daemon health, and index
    counts. Full-profile query tools reject uninitialized or degraded workspaces;
    re-call this when they do.
    """
    return ensure_workspace(_workspace_root(workspace_path)).to_payload()


@tool(ToolProfile.DEFAULT, structured_output=False)
def synapse_orient(
    terms: list[str] | None = None,
    path_scope: str | None = None,
    workspace_path: str = ".",
) -> str:
    """Start here for any code question: ranked matches for literal repository terms.

    Pass 4-8 discriminative identifiers, file names, or path fragments (up to 12)
    in the repository's own vocabulary — translate the task into likely code terms
    first, not a natural-language question. Empty terms return a repository-map
    orientation (areas, entrypoints, anchors). Returns production-first ranked
    matches with compact handles for synapse_inspect, weak candidates,
    crowded/unmatched terms, and coverage counts. Initializes the workspace
    automatically. The response is bounded server-side; unmatched terms are a
    reason to refine terms once, not to start searching. Empty results are never
    proof of absence — check coverage and unmatched_terms.
    """
    root = _navigation_workspace(workspace_path)
    request = OrientRequest(terms=tuple(terms or ()), path_scope=path_scope)
    return orient_workspace(SymbolIndex(db_path(root)), request, workspace_root=root)


@tool(ToolProfile.DEFAULT, structured_output=False)
def synapse_inspect(
    symbols: list[str],
    workspace_path: str = ".",
) -> str:
    """Inspect selected symbols using handles from synapse_orient or returned relations.

    Accepts 1-8 compact handles (s_...) or stable symbol ids; normally 2-3
    facet-diverse anchors, then follow-ups may reuse relation handles. Returns
    per symbol: the definition
    with signature and file:line, a bounded source slice (<=40 lines), parent and
    children, and grouped callers/callees/other references carrying stored
    resolution (exact|scoped|unique-name|ambiguous|unresolved), confidence, and
    usage kind verbatim, plus unresolved hypotheses. Totals and omitted counts
    stay visible; unknown inputs are listed in missing. The response is bounded
    server-side; treat the returned source as read. A complete payload is not
    proof the evidence or answer is complete — check coverage.
    """
    root = _navigation_workspace(workspace_path)
    request = InspectRequest(symbols=tuple(symbols))
    return inspect_symbols(SymbolIndex(db_path(root)), request, workspace_root=root)


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


@tool()
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


@tool()
def synapse_get_symbol_context(
    symbol_id: str,
    include_body: bool = False,
    workspace_path: str = ".",
    children_limit: int = 50,
    children_offset: int = 0,
    max_body_lines: int = 200,
) -> dict[str, object]:
    """Structural context around one symbol: parent, paged children, optional body.

    symbol_id comes from synapse_orient or synapse_get_definition. Set
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


@tool()
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
    """Read Synapse configuration: ordered ignore rules, per-rule source, and write targets.

    Self-describing; no other documentation is needed to configure Synapse. Ignore rules use
    gitignore syntax and the last matching rule decides, so the response is an ordered list
    with each rule's layer, file, and line — not a set of ignored paths. Bounded by
    rules_total/rules_complete. Safe before initialization.
    """
    workspace_root = _workspace_root(workspace_path)
    config = load_effective_config(workspace_root)
    rules = config.ignore_rules
    project_layer = config.layer(ConfigScope.PROJECT)
    return {
        "workspace_path": str(workspace_root),
        "project_config_path": str(config.project_config_path),
        "project_config_exists": config.project_config_exists,
        "global_config_path": str(config.global_config_path),
        "watch_poll_interval_s": config.watch.poll_interval_s,
        "options": {
            "ignore_rules": {
                "type": "ordered list of gitignore-style patterns",
                "semantics": "The last matching rule decides. '!' re-includes.",
                "project_source": str(project_layer.source),
                "project_ignore_file": str(config.synapseignore_path),
                "global_ignore_file": str(config.global_ignore_path),
                "accepted_forms": list(_ACCEPTED_FORMS),
                "rejected": list(_REJECTED_FORMS),
                "case_sensitive": True,
                "always_ignored": [".git"],
                "add_with": "synapse_add_ignored_directories",
                "remove_with": "synapse_remove_ignored_directories",
                "writes_to": str(config.synapseignore_path),
                "layers": [str(scope) for scope in ConfigScope],
                "takes_effect": _takes_effect(config),
                "rules": [
                    {
                        "pattern": rule.pattern,
                        "scope": str(rule.scope),
                        "origin": rule.origin,
                        "line": rule.line,
                        "negated": rule.negated,
                        "directory_only": rule.directory_only,
                    }
                    for rule in rules[:_MAX_REPORTED_RULES]
                ],
                "rules_total": len(rules),
                "rules_complete": len(rules) <= _MAX_REPORTED_RULES,
                "skipped_lines": [
                    {
                        "origin": problem.origin,
                        "line": problem.line,
                        "text": problem.text,
                        "reason": problem.reason,
                    }
                    for problem in config.ignore_problems
                ],
                "shadowed_project_json": list(project_layer.shadowed_json_entries),
                "coverage": _RULES_COVERAGE,
            },
        },
    }


@tool()
def synapse_add_ignored_directories(
    directories: list[str],
    workspace_path: str = ".",
) -> dict[str, object]:
    """Stop indexing paths; appends gitignore patterns to the project .synapseignore.

    Each entry is a gitignore pattern: a bare name matched at any depth ("node_modules"), a
    trailing slash for directories only ("build/"), a leading slash to anchor at the root
    ("/dist"), a glob ("*.min.js"), or a leading "!" to re-include. No absolute paths, no
    ".." segments. Patterns append to the end, so a new rule beats the rules already there.
    Creates .synapseignore and migrates any legacy config entries when it does not exist yet.
    Any invalid entry rejects the whole call and writes nothing. Ignored files leave the index
    on the next watch sweep; call synapse_index_workspace to apply immediately.
    """
    workspace_root = _workspace_root(workspace_path)
    requested, normalized = _normalized_directories(directories)
    result = add_ignore_patterns(workspace_root, requested, scope=ConfigScope.PROJECT)
    return _mutation_payload(workspace_root, normalized, result)


@tool()
def synapse_remove_ignored_directories(
    directories: list[str],
    workspace_path: str = ".",
) -> dict[str, object]:
    """Resume indexing paths; edits the project .synapseignore only.

    A pattern the project file owns is deleted and reported in removed. A pattern inherited
    from a built-in or the global config cannot be deleted there, so a negation is appended
    instead and reported in negated — that is how a built-in gets turned off. '.git' is the
    one exception and stays ignored. Patterns that are not ignored anywhere are reported in
    not_present and are not an error. Any invalid entry rejects the whole call and writes
    nothing. Restored files re-enter the index on the next watch sweep; call
    synapse_index_workspace to apply immediately.
    """
    workspace_root = _workspace_root(workspace_path)
    requested, normalized = _normalized_directories(directories)
    result = remove_ignore_patterns(workspace_root, requested, scope=ConfigScope.PROJECT)
    return _mutation_payload(workspace_root, normalized, result)
