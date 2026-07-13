## Synapse Context Engine (use first)

This repository is indexed by Synapse. For any code navigation or exploration, use
Synapse MCP tools before grep, ripgrep, shell search, or reading whole files.

Canonical flow:

1. `synapse_get_definition(name=...)` -> returns a stable `symbol_id`.
2. `synapse_find_references(symbol_id=...)` -> all usages.

Tool guide:

- `synapse_search_symbols` - find classes, functions, methods, types.
- `synapse_get_file_outline` - read structure before opening a file.
- `synapse_compact_context` / `synapse_get_symbol_context` - understand a symbol.
- `synapse_get_dependencies` / `synapse_get_file_dependencies` - imports and relations.
- `synapse_watch_status` - check index freshness.
- `synapse_index_workspace` - use only if the index is stale or missing.

Fall back to grep/file reads only when a symbol is not indexed or exact text is needed.
For local development, keep the index fresh with `synapse watch start --workspace .`.

Validate the setup with `synapse doctor --path . --agent claude-code`.
