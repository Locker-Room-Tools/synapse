# Synapse MCP

Synapse is a local-first, AST-based code context engine exposed over MCP. It gives AI agents
compact structural code context without uploading source code to external services.

## Quickstart

1. Use Python >=3.14.
2. Create a virtual environment and install development dependencies:
   `uv venv && uv pip install -e ".[dev]"`.
3. Initialize a workspace for your agent:
   `synapse setup codex --path .`
4. Generate a workspace-pinned MCP config:
   `synapse mcp install codex --workspace . --output ./synapse-mcp.json`
5. Verify the full MCP path:
   `synapse doctor --path . --agent codex`

The installer is guided by default. It indexes the workspace and prints next steps, but it
does not edit repository files unless you pass `--write-instructions`.

Run the MCP server over stdio with `synapse serve --workspace .` or
`python -m synapse serve --workspace .`.

## Available MCP tools

- `synapse_index_workspace`
- `synapse_search_symbols`
- `synapse_get_definition`
- `synapse_get_file_outline`
- `synapse_get_symbol_context`
- `synapse_get_dependencies`

## Agent setup helpers

- `synapse setup [claude-code|codex|opencode] [--write-instructions]`
- `synapse mcp install <client> --workspace <path> [--output <file>]`
- `synapse doctor --path <path> [--agent <client>]`

Read `docs/architecture.md` before changing the project structure.
