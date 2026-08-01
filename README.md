<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/brand/synapse-lockup-dark.svg">
    <img src="assets/brand/synapse-lockup.svg" width="320" alt="Synapse">
  </picture>
</p>

<h1 align="center">Synapse MCP</h1>

<p align="center">
  Local-first AST code context for AI agents — without uploading source code to external services.
</p>

<p align="center">
  <a href="https://github.com/Locker-Room-Tools/synapse/actions/workflows/ci.yml"><img src="https://github.com/Locker-Room-Tools/synapse/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/locker-room-tools-synapse-mcp/"><img src="https://img.shields.io/pypi/v/locker-room-tools-synapse-mcp" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-blue" alt="Python 3.12 | 3.13 | 3.14">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
</p>

## Quickstart

1. Use Python >=3.12.
2. Install Synapse as a managed CLI tool:
   `uv tool install locker-room-tools-synapse-mcp`.
3. Connect it globally to your agent:
   `synapse install codex`
4. Restart the agent once.

Replace `codex` with `claude-code`, `cline`, `continue`, `copilot`, `cursor`, `gemini`,
`hermes`, `kiro`, `opencode`, `qwen`, or `windsurf`. No repository files are created. The
first `synapse_orient` or `synapse_inspect` call initializes the local index and daemon
automatically.

See [Installation and lifecycle](docs/installation.md) for upgrades, custom scopes,
troubleshooting, and uninstall instructions. See [MCP tools](docs/tools.md) for tool contracts
and recommended agent flows.

## Development

Create a virtual environment and install the repository with development dependencies:
`uv venv && uv pip install -e ".[dev]"`. Then run `synapse grammars install` once.

Grammar installation is an explicit network operation. Indexing, watching, querying, and MCP
serving only use the local grammar cache and never download parsers implicitly.

## Available MCP tools

Synapse exposes 19 deterministic MCP tools. The default profile is exactly two:
`synapse_orient` (ranked, production-first matches for literal repository terms, with
compact symbol handles) and `synapse_inspect` (one-snapshot batch inspection of selected
symbols: definitions, bounded source, call-proven callers/callees plus neutral incoming
and outgoing references, all with stored resolution, confidence, and usage kind). Both
initialize and refresh the workspace automatically. The full
profile adds initialization, symbol lookup, definitions, references, structural context,
dependency navigation, project maps, indexing, and daemon-health tools. The complete
parameter and response reference is in [docs/tools.md](docs/tools.md).

## Configuration

Synapse reads configuration from three layers, unioned together:

| Layer | Location | Written by |
| --- | --- | --- |
| built-in defaults | packaged with Synapse | not editable |
| global user config | `~/.config/synapse/config.json` | `synapse config ignored-dirs ... --scope global` |
| project config | `<workspace>/.synapse/config.json` | agents over MCP, or `synapse config ignored-dirs ...` |

`ignored_directories` accepts a bare name matched at any depth (`node_modules`), a
root-anchored name (`/build`), or a workspace-relative path (`src/generated`). Globs,
absolute paths, and `..` segments are rejected.

Agents configure Synapse through `synapse_get_config`, `synapse_add_ignored_directories`, and
`synapse_remove_ignored_directories`, which always write the project layer. `.synapse/config.json`
is **meant to be committed** so the whole team indexes the same tree.

A change needs no reindex: the next watch sweep purges newly-ignored files and picks up
restored ones.

## Watch daemon

Synapse requires a healthy dependency-free polling daemon before query tools can read an
index. The navigation tools (or `synapse_ensure_workspace` on the full profile) start or
repair it lazily, and the MCP entry point restores
it for initialized workspaces after a reboot. Logs are written under the workspace data
directory at `logs/watch.log`, and status is written to `watch.json` in the same data directory.

A running daemon is not by itself enough to answer a question. Before serving evidence the
navigation tools also check that the index exists, the parsers are installed, and the
stored relations were produced by the current extraction semantics — a Synapse upgrade
therefore triggers one automatic rebuild rather than silently reusing stale relations.

Use `synapse watch status --workspace . --json` to inspect `running`, `backend`, `pending`, PID,
timestamps, errors, and `staleness_seconds`. Stop a detached daemon with
`synapse watch stop --workspace .`. For a bounded smoke check that performs one reconciliation
sweep and exits, run `synapse watch start --workspace . --foreground --once`.

The shipped backend is currently polling-only. Its interval defaults to the user config
`watch.poll_interval_s`; native OS event watching is intentionally deferred behind the core
`WatchBackend` protocol.

## Agent setup helpers

- `synapse install <agent> [--dry-run] [--offline] [--no-skill]` — see the
  [support matrix](docs/installation.md#supported-agents) for every supported agent id
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
