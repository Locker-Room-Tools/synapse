# MCP Tools

Synapse exposes deterministic structural data from a local AST index. Agents should use these
tools before broad text search or reading whole files.

## Workspace bootstrap

### `synapse_ensure_workspace`

Call this before the first code-navigation operation in a workspace.

- Parameters: `workspace_path="."`
- Returns: `workspace_path`, `action` (`initialized`, `reused`, or `repaired`),
  `initialized`, daemon health, and compact index counts
- Installs missing grammars, initializes or updates the index, and ensures a healthy daemon

All query tools reject uninitialized or degraded workspaces with an instruction to call this
tool. This prevents an empty SQLite database from appearing to be a valid result.

## Canonical navigation

Resolve a declaration first, then reuse its stable identifier:

```text
synapse_get_definition(name="synapse_find_references")
→ {symbol_id, file_path, line_range, ...}

synapse_find_references(symbol_id="...")
→ {items: [...], files: [...], page: {...}}
```

Use `synapse_search_symbols` only when the exact declaration name is unknown or multiple
candidates need filtering. Fall back to grep or file reads when a symbol is not indexed or
exact source text is required.

## Discovery and workspace overview

### `synapse_search_symbols`

Primary symbol lookup.

- Parameters: `query`, optional `kind`, optional `language`, `limit=20`, `offset=0`,
  `workspace_path="."`
- Returns: `items` containing compact symbol records and `page` metadata

### `synapse_project_map`

Compact workspace structure and high-value symbols.

- Parameters: `limit=50`, `offset=0`, `top_symbols_limit=20`, `workspace_path="."`
- Returns: paged project/file structure plus top symbols

### `synapse_workspace_stats`

Indexed file count, symbol count, and language mix.

- Parameters: `workspace_path="."`
- Returns: workspace statistics object

## Definitions and structure

### `synapse_get_definition`

Returns a declaration by `symbol_id` or exact `name`.

- Parameters: optional `symbol_id`, optional `name`, `limit=50`, `offset=0`,
  `workspace_path="."`
- Returns: one symbol, a paged `candidates` object for ambiguous names, or `null`
- At least one of `symbol_id` and `name` is required

### `synapse_get_file_outline`

Structural outline to call before opening a whole file.

- Parameters: `file_path`, `max_symbols=200`, `workspace_path="."`
- Returns: file metadata and nested symbols, or `null` when the file is not indexed

### `synapse_get_symbol_context`

Structural context around one symbol.

- Parameters: `symbol_id`, `include_body=false`, `children_limit=50`,
  `children_offset=0`, `workspace_path="."`
- Returns: the symbol, parent/children context, and optional body, or `null`

### `synapse_compact_context`

Minimum useful context for understanding one symbol.

- Parameters: `symbol_id`, `workspace_path="."`
- Returns: compact definition and relation context, or `null`

## References and dependencies

### `synapse_find_references`

Finds usages across the workspace.

- Parameters: optional `symbol_id`, optional `name`, `limit=50`, `offset=0`,
  `workspace_path="."`
- Returns: reference `items`, affected `files`, and `page` metadata
- Prefer `symbol_id`; use `name` only when a stable identifier is unavailable

### `synapse_get_dependencies`

Outgoing symbol relations.

- Parameters: `symbol_id`, `limit=50`, `offset=0`, `workspace_path="."`
- Returns: relation `items` and `page` metadata

### `synapse_get_file_dependencies`

File-level imports and dependencies.

- Parameters: `file_path`, `limit=50`, `offset=0`, `workspace_path="."`
- Returns: file dependency data or `null`

### `synapse_related_symbols`

Graph-like neighbors around a symbol.

- Parameters: `symbol_id`, `limit=20`, `offset=0`, `workspace_path="."`
- Returns: related symbols and paging data, or `null`

## Index health and maintenance

### `synapse_watch_status`

Read-only daemon freshness and health.

- Parameters: `workspace_path="."`
- Returns: `running`, `backend`, `degraded`, `pending`, PID, timestamps, recent errors,
  `staleness_seconds`, `initialized`, and the resolved `workspace_path`

For a new workspace, MCP starts in bootstrap mode without a daemon. For an initialized
workspace, MCP startup and `synapse_ensure_workspace` enforce daemon health.

### `synapse_index_workspace`

Explicit incremental or forced indexing.

- Parameters: `workspace_path="."`, `force=false`
- Returns: compact indexing statistics

This is a recovery and administration tool, not the first step in normal navigation. A forced
rebuild is rejected while a live watcher owns the workspace.

## Configuration

Configuration resolves as a union of three layers: packaged defaults, the global user config
(`~/.config/synapse/config.json`), and the project config (`<workspace>/.synapse/config.json`).
The MCP tools write the **project** layer only, so an ignore added for one repository never
leaks into another. The project config is meant to be committed.

An ignored directory entry takes one of three forms:

| Form | Example | Matches |
| --- | --- | --- |
| bare name | `node_modules` | a directory of that name at any depth |
| root-anchored name | `/build` | only the top-level `build/` |
| workspace-relative path | `src/generated` | only `<workspace>/src/generated/` |

Absolute paths, `.`/`..` segments, and glob patterns are rejected. Matching is case-sensitive
because entries are compared against real directory names.

Anchoring is relative to the resolved `workspace_path`. Passing a subdirectory as
`workspace_path` therefore selects a different, separately-anchored project config; every
payload echoes the resolved `workspace_path` so this stays visible.

### `synapse_get_config`

Effective configuration with per-entry provenance and write targets. Safe before
initialization.

- Parameters: `workspace_path="."`
- Returns: `workspace_path`, `project_config_path`, `project_config_exists`,
  `global_config_path`, `watch_poll_interval_s`, and an `options` map. Each option carries its
  type, `accepted_forms`, `rejected` forms, `writes_to`, `layers`, `takes_effect`, and an
  `effective` list of `{value, sources}` where `sources` is any of `built-in`, `global`,
  `project`

### `synapse_add_ignored_directories`

Stop indexing directories by writing the project config.

- Parameters: `directories` (list), `workspace_path="."`
- Returns: `added`, `already_present`, `already_covered_by_builtin`, `normalized`,
  `project_ignored_directories`, `effective_ignored_directories`, `takes_effect`

Built-in ignores are reported as already covered rather than duplicated. Any invalid entry
rejects the whole call and writes nothing.

### `synapse_remove_ignored_directories`

Resume indexing directories by removing them from the project config.

- Parameters: `directories` (list), `workspace_path="."`
- Returns: `removed`, `not_present`, `normalized`, `project_ignored_directories`,
  `effective_ignored_directories`, `takes_effect`

Built-in ignores and entries inherited from the global config cannot be removed here; both
raise an error naming where the entry comes from and how to remove it. An entry that is not
ignored anywhere is reported in `not_present` and is not an error.

### Applying a change

No reindex is required. Ignored files leave the index and restored files re-enter it on the
next watch sweep (at most `watch_poll_interval_s`). Call `synapse_index_workspace` to apply
the change immediately.

## Pagination

Paged tools accept `limit` and `offset` and return a `page` object containing the total and
continuation metadata. Preserve the same filters and workspace path while advancing `offset`.
Prefer focused queries and small pages to keep agent context compact.

## Workspace paths

The global MCP integration resolves the nearest Git root from the agent process directory.
Most calls should leave `workspace_path="."`. Project-scoped configs continue to pin one
absolute workspace, and explicit absolute paths remain available for advanced multi-workspace
use.
