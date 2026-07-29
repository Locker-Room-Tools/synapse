# Installation and Lifecycle

Synapse is installed once per user and initializes repositories lazily. The normal flow does
not add configuration or instruction files to repositories.

## Requirements

- Python 3.12 or newer
- `uv` for managed tool installation
- One of the supported agents listed in [Supported agents](#supported-agents)

## Global install

Install the published CLI and connect it to an agent:

```console
uv tool install locker-room-tools-synapse-mcp
synapse install codex
```

Replace `codex` with any id from [Supported agents](#supported-agents), then restart that
agent once. The install command:

1. Downloads missing supported tree-sitter grammars.
2. Adds a portable user-scoped MCP entry that runs `synapse serve`.
3. Adds a short marker-managed global instruction.
4. Installs the managed `synapse-code-context` skill.
5. Claude Code only: registers a suggest-only `PreToolUse` hook in
   `~/.claude/settings.json` that reminds the agent about Synapse tools when it runs
   shell exploration commands (grep/cat/find) in an indexed workspace. The hook never
   blocks or auto-approves anything.

Use `--dry-run` to detect conflicts without writing files or downloading grammars. Use
`--offline` to reject missing grammars instead of downloading them, `--no-skill` to install
only the mandatory instruction, `--no-hook` to skip the Claude Code hook, or `--force` to
replace an existing conflicting Synapse MCP entry or unmanaged skill.

## Supported agents

Every path below was verified against official documentation on 2026-07-29. Capabilities are
independent: an agent may support global MCP but no project MCP, or skills but no always-on
instruction file. Unsupported capabilities are skipped with a message, never guessed.

### Global files

| Agent | id | MCP config | Instructions | Skill | Home override |
|---|---|---|---|---|---|
| Claude Code | `claude-code` | `~/.claude.json` (hook: `~/.claude/settings.json`) | `~/.claude/CLAUDE.md` | `~/.claude/skills/synapse-code-context` | — |
| Codex | `codex` | `~/.codex/config.toml` | `~/.codex/AGENTS.md` | `~/.codex/skills/synapse-code-context` | `CODEX_HOME` |
| OpenCode | `opencode` | `~/.config/opencode/opencode.json` | `~/.config/opencode/AGENTS.md` | `~/.config/opencode/skills/synapse-code-context` | `XDG_CONFIG_HOME` |
| Hermes | `hermes` | `~/.hermes/config.yaml` | *(unsupported)* | `~/.hermes/skills/synapse-code-context` | `HERMES_HOME` |
| Gemini CLI | `gemini` | `~/.gemini/settings.json` | `~/.gemini/GEMINI.md` | `~/.gemini/skills/synapse-code-context` | — |
| GitHub Copilot CLI | `copilot` | `~/.copilot/mcp-config.json` | `~/.copilot/copilot-instructions.md` | `~/.copilot/skills/synapse-code-context` | `COPILOT_HOME` |
| Cursor | `cursor` | `~/.cursor/mcp.json` | *(unsupported)* | `~/.cursor/skills/synapse-code-context` | — |
| Windsurf | `windsurf` | `~/.codeium/windsurf/mcp_config.json` | `~/.codeium/windsurf/memories/global_rules.md` | `~/.codeium/windsurf/skills/synapse-code-context` | — |
| Cline CLI | `cline` | `~/.cline/mcp.json` | `~/.cline/data/settings/rules/synapse.md` | `~/.cline/data/settings/skills/synapse-code-context` | `CLINE_DATA_DIR` |
| Kiro CLI | `kiro` | `~/.kiro/settings/mcp.json` | `~/.kiro/steering/synapse.md` | `~/.kiro/skills/synapse-code-context` | `KIRO_HOME` |
| Qwen Code | `qwen` | `~/.qwen/settings.json` | `~/.qwen/QWEN.md` | `~/.qwen/skills/synapse-code-context` | `QWEN_HOME` |
| Continue CLI | `continue` | `~/.continue/config.yaml` | *(unsupported)* | *(unsupported)* | `CONTINUE_GLOBAL_DIR` |

Home overrides are **per path**, not per agent. `CLINE_DATA_DIR` is the clearest case: it
replaces `~/.cline/data/`, so it relocates Cline's global rules and skill but *not*
`~/.cline/mcp.json`.

### Project files

`synapse setup <agent> --path .` writes project-scoped configuration.

| Agent | MCP config | Instructions | Skill |
|---|---|---|---|
| Claude Code | `.mcp.json` | `CLAUDE.md` | *(unsupported)* |
| Codex | `.codex/config.toml` | `AGENTS.md` | *(unsupported)* |
| OpenCode | `opencode.json` | `AGENTS.md` | *(unsupported)* |
| Hermes | *(unsupported)* | `.hermes.md` | *(unsupported)* |
| Gemini CLI | `.gemini/settings.json` | `GEMINI.md` | `.gemini/skills/synapse-code-context` |
| GitHub Copilot CLI | `.github/mcp.json` | `.github/copilot-instructions.md` | `.github/skills/synapse-code-context` |
| Cursor | `.cursor/mcp.json` | `.cursor/rules/synapse.mdc` | `.cursor/skills/synapse-code-context` |
| Windsurf | *(unsupported)* | `.windsurf/rules/synapse.md` | `.windsurf/skills/synapse-code-context` |
| Cline CLI | `.cline/mcp.json` | `.cline/rules/synapse.md` | `.cline/skills/synapse-code-context` |
| Kiro CLI | `.kiro/settings/mcp.json` | `.kiro/steering/synapse.md` | `.kiro/skills/synapse-code-context` |
| Qwen Code | `.qwen/settings.json` | `QWEN.md` | `.qwen/skills/synapse-code-context` |
| Continue CLI | `.continue/mcpServers/synapse.yaml` | `.continue/rules/synapse.md` | *(unsupported)* |

Hermes and Windsurf document no project MCP path, so `synapse setup hermes` and
`synapse setup windsurf` fail with a message pointing at `synapse install`.

### Per-agent notes

- **Hermes** — Synapse never writes `SOUL.md`; Hermes documents it as personality and tone
  configuration. `.hermes.md` is the dedicated project target because Hermes loads exactly one
  project context file, preferring it over `AGENTS.md`, `CLAUDE.md`, and `.cursorrules`.
- **Copilot CLI** — both `.mcp.json` and `.github/mcp.json` are documented for the CLI.
  Synapse writes `.github/mcp.json` because `.mcp.json` is already Claude Code's project path,
  and sharing one file would make entry ownership ambiguous on uninstall. Copilot reads
  `.github/copilot-instructions.md`, `AGENTS.md`, `CLAUDE.md` and `GEMINI.md` with **no
  documented precedence** between them, so the Synapse block is additive, not an override.
- **Cursor** — the only adapter that requires an explicit `"type": "stdio"` discriminator.
  Cursor User Rules are stored by the UI with no documented file, so global instructions are
  unsupported. The project rule sets `alwaysApply: true`; Cursor ignores globs and description
  for always-applied rules.
- **Windsurf** — `docs.windsurf.com` now redirects to `docs.devin.ai` and the product is
  documented as "Devin Desktop", where `.windsurf/rules/` is a supported legacy fallback
  behind `.devin/rules/`. Synapse writes `.windsurf/` to stay consistent with the
  `~/.codeium/windsurf/` MCP and skill paths, which were not rebranded.
- **Cline CLI** — this adapter targets the CLI only. The VS Code extension keeps MCP config in
  an undocumented globalStorage location, which Synapse will not write. Two official pages
  disagree on the global rules/skills root; Synapse follows the CLI reference directory tree
  (`~/.cline/data/settings/`), which is also the only reading consistent with
  `CLINE_DATA_DIR`'s documented meaning.
- **Kiro CLI** — `inclusion: always` is documented on the IDE steering page, not the CLI
  steering page, which shows no frontmatter at all. This is a cross-surface inference.
- **Gemini CLI / Qwen Code / Copilot CLI** — the context filename is user-configurable
  (`context.fileName`), so `GEMINI.md` / `QWEN.md` are documented defaults, not guarantees.
- **Continue CLI** — `config.yaml` has mandatory `name`, `version`, and `schema` keys, so
  Synapse **never creates it**; `synapse install continue` fails with a clear message when the
  file is absent, and uninstall removes the Synapse list entry without deleting the file. The
  project target is a standalone block file Synapse owns end to end. Continue is the only
  adapter whose servers are a YAML **list** matched on an inner `name` field.

### Merge safety

JSON and YAML configs are merged structurally: installing adds or updates only the `synapse`
entry and uninstalling removes only that entry. YAML is read and written round-trip, so
comments, key order, and anchor/alias pairs in a user's `config.yaml` survive; an anchor with
no alias is dropped, which changes formatting but not meaning. Codex TOML and instruction
content use explicit managed markers. Repeated installation updates only Synapse-owned content
and preserves neighboring user configuration. Invalid configuration produces a clear error and
the file is left untouched.

### Investigated and deferred

These agents were researched against official documentation and deliberately not implemented.

| Agent | Blocker |
|---|---|
| Goose | Uses `extensions:` with `cmd`/`envs`/`env_keys` rather than an MCP server map, and `goose configure` may rewrite the file. No verified instruction, rule, or skill contract. |
| Roo Code | `.roo/mcp.json` is clean, but the global path is an undocumented VS Code globalStorage location coupled to the extension publisher id, with the filename varying between `mcp_settings.json` and `cline_mcp_settings.json`. |
| Amazon Q Developer CLI | `~/.aws/amazonq/mcp.json` is now legacy, read only when an agent sets `useLegacyMcpJson: true` (default undocumented). The current per-agent file contract has no single canonical target to merge into. |
| Zed | `settings.json` is JSONC; a plain JSON round-trip would silently delete user comments. Whether `context_servers` is settable per project is undocumented. |
| JetBrains Junie | No technical blocker — `.junie/mcp/mcp.json` and `~/.junie/mcp/mcp.json` fit the adapter model. Held back only by the scope of this release. |
| Trae | No officially documented config file path; MCP setup is an in-IDE flow. The `~/.trae/mcp.json` path in circulation comes from a third-party repository. |
| Aider | No MCP client at all — MCP support is an open feature request, so there is nothing to configure. |

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

`synapse install` never removes any project-scoped file, including `.mcp.json`,
`.codex/config.toml`, `opencode.json`, `AGENTS.md`, and `CLAUDE.md`. When a project-scoped
Synapse MCP entry exists in the current repository, install reports that it overrides the
global entry and prints a safe removal command:

```console
synapse uninstall codex --path . --scope project
```

Keep project scope when the integration is intended to be committed or pinned to one
workspace. The compatible advanced setup command remains:

```console
synapse setup codex --path .
```

It installs grammars, indexes immediately, writes project-scoped MCP, instruction, and skill
files for the capabilities that agent supports, starts the daemon, and runs doctor. Use
`--no-instructions` or `--no-skill` to skip an artifact. For an agent with no documented
project MCP path (Hermes, Windsurf) it fails and points at `synapse install` instead.

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

## Developing against a local checkout

`uv tool install <path>` builds a **snapshot**: the installed tool keeps serving that
copy no matter how much you edit the checkout afterwards. Tests then run green in the
repository while your editor's MCP host answers from stale code — validation that looks
convincing and is not.

Install the checkout as editable so the tool and the repository are the same code:

```console
uv tool install --force --editable /path/to/synapse
```

Then **restart every MCP host** (editor, agent, CLI daemon). Hosts keep the server
process alive across requests, so a replaced executable is not picked up until that
process restarts; a running watch daemon also holds the old code, so stop it with
`synapse watch stop` first if one is running.

To check which build is actually answering:

```console
synapse status --json
```

The `runtime` block reports `package_version`, `package_location` (the directory the
code was imported from), and `editable`. `editable: false` with a `source_url` pointing
at your checkout is exactly the misleading case above: a snapshot built from the repo
path rather than a live link. The same block is returned by `synapse_ensure_workspace`
and `synapse_workspace_stats`, so an agent can check it without shell access.

Synapse never modifies your Python environment on its own; run the reinstall yourself.

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
