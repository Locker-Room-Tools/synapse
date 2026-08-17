---
name: add-adapter
description: Wire a new agent adapter into Synapse — registry entry, optional hook codec, literal matrix tests, installation docs. Use when asked to add or modify support for an AI coding agent (its MCP config, instructions, skills, or hooks).
---

# Add an agent adapter

An adapter is declarative metadata in `src/synapse/cli/adapters/registry.py` interpreted by
shared machinery. Most of the work is *not* code — it is extending literal test tables and docs
that are deliberately independent of the registry. The classic failure mode is a missed surface,
so treat this as a checklist and finish every section.

## 1. Registry entry

Add one `AgentAdapter(...)` to `ADAPTERS`. The model lives in
`src/synapse/cli/adapters/model.py`: `McpTarget` (format, container shape, `key_path`,
`payload_style`, `extra_fields`, project/user `PathSpec`s), `InstructionTarget`
(`InstructionMode.BLOCK` splices a heading-anchored section into a shared file; `OWNED` writes a
dedicated file, optionally with frontmatter), `PathSpec` env overrides (`env_var` replaces
`env_prefix`; `env_var_full` replaces the whole path), and optional `HookTarget`.

Copy the closest exemplar rather than composing from scratch:

- `claude-code` — full surface (project+user MCP, both instructions, both skills, hook)
- `copilot-vscode` — minimal project-only (no user MCP, no globals, no hook)
- `hermes` — global-only, `default_scope="user"`, YAML
- `codex` — TOML, `skill_files` override (extra `agents/openai.yaml`), `warn_legacy_user_config`
- `crush` — `HookShape.FLAT`, hook settings file is the MCP config file itself
- `opencode` — `PayloadStyle.COMMAND_LIST` plus `document_defaults` (`$schema`)

`paths.py`, `render.py`, `instructions.py`, `skills.py`, and `marker_blocks.py` are
adapter-agnostic — a new adapter should need no changes there. The instruction snippets are the
shared packaged templates in `src/synapse/adapters/`; the only per-adapter difference is the
`synapse doctor --agent <id>` line (`test_agent_snippets_differ_only_in_doctor_line` enforces
this).

## 2. Hooked adapters only

A hook needs both the registry `HookTarget` and a new `HookCodec` in
`src/synapse/cli/hooks/codecs.py` (`CODECS` is keyed by the `HookTarget.codec` string). Codecs
must be fail-open: always exit 0, never block the agent's tool call, and preserve any
allow/permission decision the agent expects (see `qwen-pre-bash` for an explicit
`permissionDecision: allow`). Then update the literal rosters:

- `tests/cli/test_adapter_cli.py` — `expected = ["claude-code", "crush", "qwen"]` hook list
- `tests/cli/test_hooks.py` — add a `SHELL_TOOLS` entry for the new codec

## 3. Literal matrix tests

`tests/cli/test_adapter_matrix.py` states its own design: expectations are written out literally
"so a registry typo cannot make these tests agree with themselves". Never derive these values
from the registry — type them from the target agent's documentation:

- `EXPECTED_PATHS[id]` — the 6-tuple `(project mcp, user mcp, project instructions,
  global instructions, project skill, global skill)`, `None` for unsupported surfaces
- `EXPECTED_ENTRIES[id]` — the exact serialized MCP entry the agent's config should contain
- `_SEEDS` — only if the adapter introduces a genuinely new `(format, shape)` combination

`test_matrix_covers_every_adapter` gates completeness against `ADAPTERS` and
`adapter_choices()`, so forgetting a table fails loudly.

## 4. Other test files to check

- `tests/cli/test_adapters.py` — shared-file groups are hardcoded (the `AGENTS.md` sharers
  tuple, `test_both_copilot_adapters_keep_a_single_block`); extend them if the new adapter
  shares an instruction file or skills directory.
- Grep `tests/cli/test_setup.py`, `test_global_install.py`, `test_doctor.py`,
  `test_init_status.py`, `test_managed_workflow.py` for hardcoded agent ids before finishing.
- `tests/cli/conftest.py` fixtures derive from the registry; usually no edit unless a new
  env-override kind is introduced.

## 5. Shared-skill edge

Several adapters share one skills directory (`.agents/skills/`, `.github/skills/`). The
keep-until-last-uninstall behavior is computed live by
`agents_sharing_skill` in `src/synapse/cli/installer.py` — same resolved skill path *and* the
other adapter's MCP config still contains a Synapse entry. A new sharing adapter needs no extra
code, but must be named in the shared-file prose of `docs/installation.md` and covered by the
shared-skill tests.

## 6. Docs

`docs/installation.md`:

- "Global files" table (agent, id, user MCP path + hook file, global instructions/skill, env
  override) and "Project files" table
- shared-file/shared-skill prose
- one "Per-agent notes" bullet explaining the adapter's quirk
- "Hooks" table if hooked — only context-injecting, allow-preserving hooks qualify
- remove the agent's row from "Investigated and deferred" if present

Add a `CHANGELOG.md` entry under `## [Unreleased]`. `README.md` links the support matrix rather
than duplicating it; check its agent-count phrasing.

## 7. Gate

```bash
uv run ruff check && uv run mypy && uv run pytest -q tests/cli
```
