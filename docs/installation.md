# Installation and Lifecycle

Synapse is installed once per user and initializes repositories lazily. The normal flow does
not add configuration or instruction files to repositories.

## Requirements

- Python 3.12 or newer
- `uv` for managed tool installation
- Claude Code, Codex, or OpenCode

## Global install

Install the published CLI and connect it to an agent:

```console
uv tool install locker-room-tools-synapse-mcp
synapse install codex
```

Replace `codex` with `claude-code` or `opencode`, then restart that agent once. The install
command:

1. Downloads missing supported tree-sitter grammars.
2. Adds a portable user-scoped MCP entry that runs `synapse serve`.
3. Adds a short marker-managed global instruction.
4. Installs the managed `synapse-code-context` skill.

Use `--dry-run` to detect conflicts without writing files or downloading grammars. Use
`--offline` to reject missing grammars instead of downloading them, `--no-skill` to install
only the mandatory instruction, or `--force` to replace an existing conflicting Synapse MCP
entry or unmanaged skill.

## Global files

| Agent | MCP config | Instructions | Skill |
|---|---|---|---|
| Claude Code | `~/.claude.json` | `~/.claude/CLAUDE.md` | `~/.claude/skills/synapse-code-context` |
| Codex | `~/.codex/config.toml` | `~/.codex/AGENTS.md` | `~/.codex/skills/synapse-code-context` |
| OpenCode | `~/.config/opencode/opencode.json` | `~/.config/opencode/AGENTS.md` | `~/.config/opencode/skills/synapse-code-context` |

`CODEX_HOME` and `XDG_CONFIG_HOME` are respected. JSON configs are merged structurally.
Codex TOML and instruction content use explicit managed markers. Repeated installation
updates only Synapse-owned content and preserves neighboring user configuration.

## First request in a repository

The global instruction tells the agent to call:

```text
synapse_ensure_workspace(workspace_path=".")
```

The MCP server resolves the nearest Git root from its current directory, or uses that
directory when no Git root exists. Ensure
installs any newly required grammar, creates or updates the index, starts the detached daemon,
and returns `initialized`, `reused`, or `repaired`. Query tools reject uninitialized or
degraded workspaces instead of creating an empty index.

The same lifecycle is available manually:

```console
synapse init --path .
synapse status --path . --json
```

`status` is read-only and reports `uninitialized`, `initializing`, `ready`, or `degraded`
without creating cache directories. `init --dry-run` previews initialization, and
`init --offline` forbids grammar downloads.

## Data and daemon lifecycle

SQLite indexes, metadata, daemon status, journal, and logs are stored outside repositories in
a deterministic per-workspace data directory. Multiple repositories receive independent
indexes and daemon processes.

For an initialized workspace, MCP startup restores a missing daemon before exposing query
tools. A new workspace may start MCP without a daemon so `synapse_ensure_workspace` remains
available. Once initialized, daemon failure is a hard error.

Manual diagnostics remain available:

```console
synapse watch status --workspace . --json
synapse watch restart --workspace .
synapse doctor --path . --agent codex
```

## Existing project-scoped installations

`synapse install` never removes `.mcp.json`, `.codex/config.toml`, `opencode.json`,
`AGENTS.md`, or `CLAUDE.md`. When a project-scoped Synapse MCP entry exists in the current
repository, install reports that it overrides the global entry and prints a safe removal
command:

```console
synapse uninstall codex --path . --scope project
```

Keep project scope when the integration is intended to be committed or pinned to one
workspace. The compatible advanced setup command remains:

```console
synapse setup codex --path .
```

It installs grammars, indexes immediately, writes project-scoped MCP and instruction files,
starts the daemon, and runs doctor.

## Upgrading from `synapse-mcp`

The distribution was renamed while the executable and Python package stayed the same:

```console
uv tool uninstall synapse-mcp
uv tool install --upgrade locker-room-tools-synapse-mcp
synapse install codex
synapse --version
```

Restart the agent after updating its global MCP integration.

If `synapse` raises `ModuleNotFoundError: No module named 'synapse'` instead of running,
`~/.local/bin/synapse` resolves to a stale shim rather than a real install — most often an old
**editable** `uv tool install synapse-mcp` whose source checkout was moved or deleted. Confirm
with:

```console
which -a synapse
uv tool list
```

Uninstall the stale tool (`uv tool uninstall synapse-mcp`) and reinstall as shown above. If a
`pipx` install of `locker-room-tools-synapse-mcp` is also present, keep only one manager on
`PATH` — a second manager cannot claim `~/.local/bin/synapse` while another owns it.

## Uninstall

Remove global MCP, instruction, and skill artifacts:

```console
synapse uninstall codex --global
```

Use `--keep-config`, `--keep-instructions`, or `--keep-skill` to retain an artifact. The
command removes only the portable managed MCP entry and managed instruction/skill content.
Pinned or unmanaged entries are reported and preserved.

Uninstall does not delete workspace indexes or daemon data. Stop a workspace daemon
separately when it is no longer needed:

```console
synapse watch stop --workspace .
```

## Why the skill does not install MCP

Skills can lazily install ordinary CLI workflows, as Graphify does. MCP tools are discovered
when the agent client starts, so a skill that adds MCP configuration during a request cannot
make those tools appear reliably in the same session. Synapse therefore installs MCP once
through `synapse install`; the skill only supplies the detailed navigation workflow.

## Advanced commands

- `synapse setup` creates a complete project-scoped integration.
- `synapse mcp install` renders or writes an individual MCP config.
- `synapse index` performs an explicit incremental or forced index operation.
- `synapse grammars install` fills the parser cache.
- `synapse serve` is the client-managed MCP stdio entry point.
- Running `synapse` without arguments shows help and the global install command.
