## Synapse Context Engine (use first)

This repository is indexed by Synapse. Before the first code navigation in a session, call
`synapse_ensure_workspace`, then use Synapse MCP tools before grep, ripgrep, shell search,
or reading whole files.

Canonical flow:

1. `synapse_ensure_workspace()` -> initializes or repairs the workspace; continue when healthy.
2. `synapse_get_definition(name=...)` -> returns a stable `symbol_id`.
3. `synapse_find_references(symbol_id=...)` -> all usages.

Tool guide:

- `synapse_search_symbols` - find classes, functions, methods, types when the exact name is unknown.
- `synapse_get_file_outline` - file structure before opening a file.
- `synapse_project_map` / `synapse_workspace_stats` - workspace overview and index statistics.
- `synapse_compact_context` / `synapse_get_symbol_context` - understand one symbol.
- `synapse_get_dependencies` / `synapse_get_file_dependencies` / `synapse_related_symbols` -
  outgoing relations, file imports, and graph neighbors.
- `synapse_watch_status` - read-only freshness and daemon health diagnosis.
- `synapse_index_workspace` - recovery and administration only, never the first step.

If a query reports an uninitialized or degraded workspace, call `synapse_ensure_workspace`
again. Fall back to grep or file reads only for exact text or content Synapse does not index.

Validate the setup with `synapse doctor --path . --agent claude-code`.
