## Synapse Context Engine (use first)

This repository is indexed by Synapse. Before the first code navigation in a session, call
`synapse_ensure_workspace`, then use Synapse MCP tools instead of grep, ripgrep, shell
search, or reading whole files:

- Repository layout (`ls -R`, `find`, `tree`) -> `synapse_project_map`
- Find a symbol (`grep -r "Name"`) -> `synapse_search_symbols` or `synapse_get_definition`
- Find usages -> `synapse_find_references(symbol_id=...)`
- File structure (`cat`, reading a whole file) -> `synapse_get_file_outline`
- Read an implementation -> `synapse_get_symbol_context(symbol_id=..., include_body=True)`

Canonical flow:

1. `synapse_ensure_workspace()` -> initializes or repairs the workspace; continue when healthy.
2. `synapse_get_definition(name=...)` -> returns a stable `symbol_id`.
3. `synapse_find_references(symbol_id=...)` -> all usages.
4. `synapse_get_symbol_context(symbol_id=..., include_body=True)` -> the implementation source.

Tool guide:

- `synapse_search_symbols` - find classes, functions, methods, types when the exact name is unknown.
- `synapse_get_file_outline` - file structure with signatures before opening a file.
- `synapse_project_map` / `synapse_workspace_stats` - workspace overview and index statistics.
- `synapse_compact_context` / `synapse_get_symbol_context` - understand one symbol;
  `include_body=True` returns the implementation without a file read.
- `synapse_get_dependencies` / `synapse_get_file_dependencies` / `synapse_related_symbols` -
  outgoing relations, file imports, and graph neighbors.
- `synapse_watch_status` - read-only freshness and daemon health diagnosis.
- `synapse_index_workspace` - recovery and administration only, never the first step.

If Synapse tools are deferred, load them together in a single ToolSearch call before
exploring; never fall back to shell search because tool schemas are not loaded yet. If a
query reports an uninitialized or degraded workspace, call `synapse_ensure_workspace`
again. Use grep or file reads only for exact text matching or content Synapse does not
index (unsupported languages, generated files).

Validate the setup with `synapse doctor --path . --agent {agent_id}`.
