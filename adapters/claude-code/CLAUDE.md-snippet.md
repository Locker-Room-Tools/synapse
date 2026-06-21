## Synapse Context Engine

This repository uses Synapse for local code intelligence.

Before using grep, ripgrep, shell search, or reading large source files, prefer Synapse MCP tools:

- Use `synapse_search_symbols` to find classes, functions, methods, interfaces, records, structs, and other symbols.
- Use `synapse_get_definition` to locate a symbol declaration.
- Use `synapse_get_file_outline` before reading an entire file.
- Use `synapse_get_symbol_context` to retrieve compact context around a symbol.
- Use `synapse_get_dependencies` to inspect what a symbol contains or imports.
- Use `synapse_index_workspace` if the index is missing or stale.

Validate the setup with `synapse doctor --path . --agent claude-code`.
