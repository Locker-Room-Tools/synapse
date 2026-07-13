# Synapse MCP

Synapse is a local-first, AST-based code context engine exposed over MCP. It gives AI agents
compact structural code context without uploading source code to external services.

## Quickstart

1. Use Python >=3.12.
2. Create a virtual environment and install development dependencies:
   `uv venv && uv pip install -e ".[dev]"`.
3. Initialize a workspace for your agent:
   `synapse setup codex --path .`
4. Install a workspace-pinned MCP config into the client default path:
   `synapse mcp install codex --workspace .`
5. Verify the full MCP path:
   `synapse doctor --path . --agent codex`

The installer is guided by default. It indexes the workspace and prints next steps, but it
does not edit repository files unless you pass `--write-instructions`.
Use `synapse mcp install <client> --workspace . --print` to print config without writing,
or `--dry-run` to preview the resolved write. Remove managed config and instructions with
`synapse uninstall <client> --path .`.

Run the MCP server over stdio with `synapse serve --workspace .` or
`python -m synapse serve --workspace .`.

## Available MCP tools

- `synapse_index_workspace`
- `synapse_search_symbols`
- `synapse_get_definition`
- `synapse_get_file_outline`
- `synapse_workspace_stats`
- `synapse_watch_status`
- `synapse_project_map`
- `synapse_get_file_dependencies`
- `synapse_get_symbol_context`
- `synapse_get_dependencies`
- `synapse_find_references`
- `synapse_related_symbols`
- `synapse_compact_context`

## Watch daemon

Synapse can keep an index fresh with a dependency-free polling daemon. Start it in the
background with `synapse watch start --workspace .`; logs are written under the workspace data
directory at `logs/watch.log`, and status is written to `watch.json` in the same data directory.

Use `synapse watch status --workspace . --json` to inspect `running`, `backend`, `pending`, PID,
timestamps, errors, and `staleness_seconds`. Stop a detached daemon with
`synapse watch stop --workspace .`. For a bounded smoke check that performs one reconciliation
sweep and exits, run `synapse watch start --workspace . --foreground --once`.

The shipped backend is currently polling-only. Its interval defaults to the user config
`watch.poll_interval_s`; native OS event watching is intentionally deferred behind the core
`WatchBackend` protocol.

## Agent setup helpers

- `synapse setup [claude-code|codex|opencode] [--write-instructions]`
- `synapse mcp install <client> --workspace <path> [--scope project|user] [--print]`
- `synapse uninstall <client> --path <path> [--scope project|user]`
- `synapse doctor --path <path> [--agent <client>] [--scope project|user]`

Read `docs/architecture.md` before changing the project structure.
