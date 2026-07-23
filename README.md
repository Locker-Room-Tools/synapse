# Synapse MCP

Synapse is a local-first, AST-based code context engine exposed over MCP. It gives AI agents
compact structural code context without uploading source code to external services.

## Quickstart

1. Use Python >=3.12.
2. Install Synapse as a managed CLI tool:
   `uv tool install locker-room-tools-synapse-mcp`.
3. Connect it globally to your agent:
   `synapse install codex`
4. Restart the agent once.

Replace `codex` with `claude-code` or `opencode`. No repository files are created. On the first
code-navigation request, the global instruction calls `synapse_ensure_workspace`, which
initializes the local index and daemon automatically.

See [Installation and lifecycle](docs/installation.md) for upgrades, custom scopes,
troubleshooting, and uninstall instructions. See [MCP tools](docs/tools.md) for tool contracts
and recommended agent flows.

## Development

Create a virtual environment and install the repository with development dependencies:
`uv venv && uv pip install -e ".[dev]"`. Then run `synapse grammars install` once.

Grammar installation is an explicit network operation. Indexing, watching, querying, and MCP
serving only use the local grammar cache and never download parsers implicitly.

## Available MCP tools

Synapse exposes 14 deterministic MCP tools for initialization, symbol lookup, definitions, references,
structural context, dependency navigation, project maps, indexing, and daemon health. The
complete parameter and response reference is in [docs/tools.md](docs/tools.md).

## Watch daemon

Synapse requires a healthy dependency-free polling daemon before query tools can read an
index. `synapse_ensure_workspace` starts or repairs it lazily, and the MCP entry point restores
it for initialized workspaces after a reboot. Logs are written under the workspace data
directory at `logs/watch.log`, and status is written to `watch.json` in the same data directory.

Use `synapse watch status --workspace . --json` to inspect `running`, `backend`, `pending`, PID,
timestamps, errors, and `staleness_seconds`. Stop a detached daemon with
`synapse watch stop --workspace .`. For a bounded smoke check that performs one reconciliation
sweep and exits, run `synapse watch start --workspace . --foreground --once`.

The shipped backend is currently polling-only. Its interval defaults to the user config
`watch.poll_interval_s`; native OS event watching is intentionally deferred behind the core
`WatchBackend` protocol.

## Agent setup helpers

- `synapse install <claude-code|codex|opencode> [--dry-run] [--offline] [--no-skill]`
- `synapse init --path <path> [--dry-run] [--offline]`
- `synapse status --path <path> [--json]`
- `synapse uninstall <client> --global`
- `synapse setup <client> --path <path>` for advanced project-scoped integration
- `synapse mcp install <client> --workspace <path> [--scope project|user] [--print]`
- `synapse uninstall <client> --path <path> [--scope project|user]`
- `synapse doctor --path <path> [--agent <client>] [--scope project|user]`

Project setup, `mcp install`, manual indexing, `serve`, and foreground watch mode remain
available for advanced integration and diagnostics.

Read `docs/architecture.md` before changing the project structure.
