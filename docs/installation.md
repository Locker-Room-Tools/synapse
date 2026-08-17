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
5. Where the agent supports it, registers a suggest-only pre-shell hook that reminds the
   agent about Synapse tools when it runs shell exploration commands (grep/cat/find) in an
   indexed workspace. The hook never blocks or auto-approves anything, and stays silent on
   any error. See [Hooks](#hooks) for which agents qualify.

Use `--dry-run` to detect conflicts without writing files or downloading grammars. Use
`--offline` to reject missing grammars instead of downloading them, `--no-skill` to install
only the mandatory instruction, `--no-hook` to skip the hook, or `--force` to
replace an existing conflicting Synapse MCP entry or unmanaged skill.

## Supported agents

Every path below was verified against official documentation on 2026-08-17, and against upstream
source code where the documentation was ambiguous or wrong. Capabilities are independent: an
agent may support global MCP but no project MCP, or skills but no always-on instruction file.
Unsupported capabilities are skipped with a message, never guessed.

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
| Cline | `cline` | `~/.cline/data/settings/cline_mcp_settings.json` | `~/.cline/rules/synapse.md` | `~/.cline/skills/synapse-code-context` | `CLINE_DATA_DIR`, `CLINE_MCP_SETTINGS_PATH` |
| Kiro CLI | `kiro` | `~/.kiro/settings/mcp.json` | `~/.kiro/steering/synapse.md` | `~/.kiro/skills/synapse-code-context` | `KIRO_HOME` |
| Qwen Code | `qwen` | `~/.qwen/settings.json` (hook: same file) | `~/.qwen/QWEN.md` | `~/.qwen/skills/synapse-code-context` | `QWEN_HOME` |
| Continue CLI | `continue` | `~/.continue/config.yaml` | *(unsupported)* | *(unsupported)* | `CONTINUE_GLOBAL_DIR` |
| Kimi Code CLI | `kimi` | `~/.kimi-code/mcp.json` | `~/.kimi-code/AGENTS.md` | `~/.kimi-code/skills/synapse-code-context` | `KIMI_CODE_HOME` |
| Factory Droid | `droid` | `~/.factory/mcp.json` | `~/.factory/AGENTS.md` | `~/.factory/skills/synapse-code-context` | — |
| Crush | `crush` | `~/.config/crush/crush.json` (hook: same file) | `~/.config/crush/CRUSH.md` | `~/.config/crush/skills/synapse-code-context` | `XDG_CONFIG_HOME` |
| Amp | `amp` | `~/.config/amp/settings.json` | `~/.config/amp/AGENTS.md` | `~/.config/amp/skills/synapse-code-context` | `XDG_CONFIG_HOME` |
| Zed | `zed` | `~/.config/zed/settings.json` | `~/.config/zed/AGENTS.md` | `~/.agents/skills/synapse-code-context` | `XDG_CONFIG_HOME` |
| Google Antigravity | `antigravity` | `~/.gemini/config/mcp_config.json` | `~/.gemini/GEMINI.md` | `~/.gemini/config/skills/synapse-code-context` | — |
| Goose | `goose` | `~/.config/goose/config.yaml` | `~/.config/goose/.goosehints` | `~/.agents/skills/synapse-code-context` | `XDG_CONFIG_HOME` |
| OpenClaw | `openclaw` | `~/.openclaw/openclaw.json` | *(unsupported)* | `~/.openclaw/skills/synapse-code-context` | `OPENCLAW_HOME` |
| GitHub Copilot (VS Code) | `copilot-vscode` | *(unsupported)* | *(unsupported)* | *(unsupported)* | — |
| Roo Code | `roo` | *(unsupported)* | *(unsupported)* | `~/.roo/skills/synapse-code-context` | — |

Home overrides are **per path**, not per agent. Cline is the clearest case: `CLINE_DATA_DIR`
replaces `~/.cline/data/`, which holds only the MCP settings file, while the global rules and
skill sit directly under `~/.cline/` and do not move. `CLINE_MCP_SETTINGS_PATH` overrides the
MCP settings file outright and wins over `CLINE_DATA_DIR`.

Copilot (VS Code) and Roo Code are project-scope only, so `synapse install copilot-vscode`
reports its global MCP config as unsupported and writes nothing; use
`synapse setup <agent> --path .` instead.

### Project files

`synapse setup <agent> --path .` writes project-scoped configuration.

| Agent | MCP config | Instructions | Skill |
|---|---|---|---|
| Claude Code | `.mcp.json` | `CLAUDE.md` | `.claude/skills/synapse-code-context` |
| Codex | `.codex/config.toml` | `AGENTS.md` | *(unsupported)* |
| OpenCode | `opencode.json` | `AGENTS.md` | *(unsupported)* |
| Hermes | *(unsupported)* | `.hermes.md` | *(unsupported)* |
| Gemini CLI | `.gemini/settings.json` | `GEMINI.md` | `.gemini/skills/synapse-code-context` |
| GitHub Copilot CLI | `.github/mcp.json` | `.github/copilot-instructions.md` | `.github/skills/synapse-code-context` |
| Cursor | `.cursor/mcp.json` | `.cursor/rules/synapse.mdc` | `.cursor/skills/synapse-code-context` |
| Windsurf | *(unsupported)* | `.windsurf/rules/synapse.md` | `.windsurf/skills/synapse-code-context` |
| Cline | `.cline/mcp.json` | `.cline/rules/synapse.md` | `.cline/skills/synapse-code-context` |
| Kiro CLI | `.kiro/settings/mcp.json` | `.kiro/steering/synapse.md` | `.kiro/skills/synapse-code-context` |
| Qwen Code | `.qwen/settings.json` | `QWEN.md` | `.qwen/skills/synapse-code-context` |
| Continue CLI | `.continue/mcpServers/synapse.yaml` | `.continue/rules/synapse.md` | *(unsupported)* |
| Kimi Code CLI | `.kimi-code/mcp.json` | `AGENTS.md` | `.kimi-code/skills/synapse-code-context` |
| Factory Droid | `.factory/mcp.json` | `AGENTS.md` | `.factory/skills/synapse-code-context` |
| Crush | `crush.json` | `CRUSH.md` | `.crush/skills/synapse-code-context` |
| Amp | `.amp/settings.json` | `AGENTS.md` | `.agents/skills/synapse-code-context` |
| Zed | `.zed/settings.json` | `.rules` | `.agents/skills/synapse-code-context` |
| Google Antigravity | `.agents/mcp_config.json` | `.agents/rules/synapse.md` | `.agents/skills/synapse-code-context` |
| Goose | *(unsupported)* | `.goosehints` | `.agents/skills/synapse-code-context` |
| OpenClaw | *(unsupported)* | *(unsupported)* | *(unsupported)* |
| GitHub Copilot (VS Code) | `.vscode/mcp.json` | `.github/copilot-instructions.md` | `.github/skills/synapse-code-context` |
| Roo Code | `.roo/mcp.json` | `.roo/rules/synapse.md` | `.roo/skills/synapse-code-context` |

Hermes, Windsurf, Goose and OpenClaw document no project MCP path, so `synapse setup` for those
agents fails with a message pointing at `synapse install`.

Several adapters share `AGENTS.md` (Codex, OpenCode, Kimi, Amp, Droid), and both Copilot
adapters share `.github/copilot-instructions.md`. The file keeps **exactly one** Synapse block
no matter how many of them you install: a block Synapse already owns is replaced in place. Text
Synapse does not own is never overwritten — the block is appended after it.

The same sharing applies to skills directories (`.agents/skills/` for Amp, Zed, and Goose;
`.github/skills/` for both Copilot adapters): uninstalling one agent keeps the shared skill as
long as another installed agent still resolves to it, and the last uninstall removes it.

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
- **Cline** — Cline's own docs advertise `~/.cline/mcp.json`, but its shared resolver reads
  `~/.cline/data/settings/cline_mcp_settings.json`; the discrepancy is a known upstream docs bug
  ([cline#11671](https://github.com/cline/cline/issues/11671)). Synapse follows the code. Because
  `~/.cline/` is documented as applying across the CLI, IDE and SDK, this one adapter covers the
  VS Code extension too; the historical extension-local globalStorage file is never written.
  **Upgrading from 0.5.4 or earlier:** that release wrote `~/.cline/mcp.json`,
  `~/.cline/data/settings/rules/synapse.md` and `~/.cline/data/settings/skills/`. Those paths are
  no longer managed, so delete them by hand — or run `synapse uninstall cline --global` on the
  older version *before* upgrading.
- **Kiro CLI** — `inclusion: always` is documented on the IDE steering page, not the CLI
  steering page, which shows no frontmatter at all. This is a cross-surface inference.
- **Gemini CLI / Qwen Code / Copilot CLI** — the context filename is user-configurable
  (`context.fileName`), so `GEMINI.md` / `QWEN.md` are documented defaults, not guarantees.
- **Continue CLI** — `config.yaml` has mandatory `name`, `version`, and `schema` keys, so
  Synapse **never creates it**; `synapse install continue` fails with a clear message when the
  file is absent, and uninstall removes the Synapse list entry without deleting the file. The
  project target is a standalone block file Synapse owns end to end. Continue is the only
  adapter whose servers are a YAML **list** matched on an inner `name` field.
- **Goose** — the only adapter that spells the executable `cmd` instead of `command`, and its
  entries carry a required, non-defaulted `enabled: true`. MCP config is global-only, but Goose
  does read a project `.goosehints`, so project instructions and skill are still supported.
- **Crush** — every MCP entry needs `"type": "stdio"`, and the schema is
  `additionalProperties: false`, so Synapse emits exactly `type`, `command`, `args`. Hooks and
  MCP servers share one `crush.json`; installing both leaves a single file and uninstalling both
  removes it. Upstream now prefers a `crushrc` shell script and documents `crush.json` as
  deprecated but still supported — Synapse writes the JSON form.
- **Amp** — servers live under the single literal settings key `"amp.mcpServers"` (one key
  containing a dot, not a nested path), which is why the same key works inside VS Code settings.
  Workspace-config servers require in-app approval before Amp will start them. Amp documents no
  `.amp/skills/`, so the project skill goes to the shared `.agents/skills/`.
- **Zed** — Zed picks the **first** match from a fixed list of nine instruction filenames, in
  which `.rules` ranks first, so Synapse writes `.rules` to guarantee the block is the file Zed
  reads. Zed settings are JSONC; Synapse writes strict JSON, which Zed accepts, but a config
  that already contains comments cannot be round-tripped and is reported as an error rather than
  rewritten. The `"source": "custom"` field seen in older third-party guides is absent from
  current docs and is not emitted.
- **Google Antigravity** — the global skills directory is `~/.gemini/config/skills/`, *not* the
  `~/.agents/skills/` that Zed and Goose use. Synapse writes the owned `.agents/rules/synapse.md`
  rather than joining `AGENTS.md`, keeping one fewer writer on that shared file.
- **OpenClaw** — a single global config, with servers nested under `mcp.servers` rather than a
  top-level `mcpServers`. Its config format is JSON5, but OpenClaw itself reserializes to strict
  JSON on write, so Synapse's strict-JSON round-trip loses nothing it would have kept. OpenClaw's
  "workspace" is the agent home (`~/.openclaw/workspace`), not your repository, so there is no
  project instruction target — a repo-root `AGENTS.md` would never be read.
- **GitHub Copilot (VS Code)** — reads `.vscode/mcp.json` under the root key **`servers`**, not
  `mcpServers`. Project scope only: the user-profile `mcp.json` path is undocumented and moves
  with VS Code builds and profiles, so Synapse will not guess it. This adapter shares
  `.github/copilot-instructions.md` and `.github/skills/` with the `copilot` CLI adapter.
- **Roo Code** — project MCP only. The global `mcp_settings.json` lives under a storage base the
  user can relocate, so it is not safely addressable; the global *skills* directory `~/.roo/skills/`
  is a documented plain path and is supported. Roo rule files use no frontmatter convention.

### Merge safety

JSON and YAML configs are merged structurally: installing adds or updates only the `synapse`
entry and uninstalling removes only that entry. YAML is read and written round-trip, so
comments, key order, and anchor/alias pairs in a user's `config.yaml` survive; an anchor with
no alias is dropped, which changes formatting but not meaning. Codex TOML and instruction
content use explicit managed markers. Repeated installation updates only Synapse-owned content
and preserves neighboring user configuration. Invalid configuration produces a clear error and
the file is left untouched.

### Hooks

The Synapse hook is **suggest-only**: it adds a line of context reminding the agent to prefer
`synapse_orient`/`synapse_inspect` when it is about to run `grep`, `rg`, `cat`, `find` or `tree`
in an indexed workspace. It never blocks, never auto-approves, and returns silently on any error.

An agent therefore qualifies only if its hook system can **inject context while allowing the
call**. A hook that can only block or deny cannot express a nudge, and Synapse will not repurpose
a denial to deliver one.

| Agent | Hook file | Qualifies |
|---|---|---|
| Claude Code | `~/.claude/settings.json` | yes — `hookSpecificOutput.additionalContext` |
| Crush | `~/.config/crush/crush.json` | yes — `context`, with the decision omitted so the normal permission prompt still runs |
| Qwen Code | `~/.qwen/settings.json` | yes — `additionalContext`, alongside a required explicit `"allow"` decision |
| Gemini CLI | — | no — has no `PreToolUse` event; `BeforeTool` has no `additionalContext` |
| Factory Droid | — | no — docs explicitly exclude `additionalContext` from `PreToolUse` |
| Cursor | — | no — `agent_message` is documented only for denials |
| Cline | — | no — context lands on the *next* turn, too late to redirect the current call |

Crush and Qwen keep hooks in the same file as their MCP config; installing both leaves one file,
and uninstalling both removes it. Skip the hook with `synapse install <agent> --no-hook`, and
keep it on uninstall with `synapse uninstall <agent> --global --keep-hook`.

Qwen's documentation states that `PreToolUse` supports context injection, but every worked
example pairs `additionalContext` with a `deny` decision, so delivery on the allow path is
unconfirmed upstream. If your Qwen build ignores it, `--no-hook` disables it cleanly.

### Investigated and deferred

These agents were researched against official documentation and deliberately not implemented.

| Agent | Blocker |
|---|---|
| Amazon Q Developer CLI | `~/.aws/amazonq/mcp.json` is now legacy, read only when an agent sets `useLegacyMcpJson: true` (default undocumented). The current per-agent file contract has no single canonical target to merge into. |
| JetBrains Junie | No technical blocker — `.junie/mcp/mcp.json` and `~/.junie/mcp/mcp.json` fit the adapter model. Held back only by the scope of this release. |
| Trae | No officially documented config file path; MCP setup is an in-IDE flow. The `~/.trae/mcp.json` path in circulation comes from a third-party repository. |
| Aider | No MCP client at all — MCP support is an open feature request, so there is nothing to configure. |

## First request in a repository

The first navigation call initializes the workspace lazily:

```text
synapse_orient(terms=["..."])
```

The MCP server resolves the nearest Git root from its current directory, or uses that
directory when no Git root exists. When the workspace is uninitialized or degraded, the
navigation tools install any newly required grammar, create or update the index, and
start the detached daemon before answering. On `--profile full`,
`synapse_ensure_workspace` performs the same lifecycle explicitly, and full-profile
query tools reject uninitialized or degraded workspaces instead of creating an empty
index.

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
tools. A new workspace may start MCP without a daemon; the navigation tools (and
`synapse_ensure_workspace` on the full profile) initialize it on first use. Once
initialized, daemon failure is a hard error.

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
