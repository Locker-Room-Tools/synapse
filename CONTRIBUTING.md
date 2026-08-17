# Contributing to Synapse

Thanks for helping improve Synapse. This file covers the human workflow: environment,
the quality gate, and the two changes contributors make most often (adding a language,
adding an MCP tool).

[AGENTS.md](AGENTS.md) is the architecture contract for this repository and
[docs/architecture.md](docs/architecture.md) explains the module layout. Read both before
changing project structure.

The recurring maintenance tasks each have a step-by-step checklist under
[.agents/skills/](.agents/skills/) (adding a language, adding an agent adapter, adding an
MCP tool, changing index persistence, cutting a release, running an A/B evaluation).
They use the cross-agent SKILL.md format in the shared Agent Skills catalog, which most
coding agents (Claude Code, Codex, Copilot, Cursor, Amp, Zed, Goose, and others) discover
automatically; for everyone else they are plain markdown checklists that stay in sync
with the test suites they cite.

## Setup

```bash
uv venv && uv pip install -e ".[dev]"
```

Then install the parser binaries once:

```bash
synapse grammars install
```

Grammar installation is the only network operation in the project, and it is always
explicit. Indexing, watching, querying, and MCP serving read the local grammar cache and
never download anything implicitly — that guarantee is what "local-first" means here, so
do not add network calls to the index path.

Optionally install the pre-commit hooks, which run ruff and mypy on staged changes:

```bash
pre-commit install
```

## Quality gate

Run the same four checks CI runs. A change is ready when all of them pass:

```bash
uv run ruff check
```

```bash
uv run ruff format --check
```

```bash
uv run mypy
```

```bash
uv run pytest -q --cov=synapse
```

`mypy` runs in `--strict` mode over both `src` and `tests`; every signature and dataclass
needs full type hints. Coverage has an 85% floor. No new errors, no lowered floor.

CI additionally runs the suite on Ubuntu across Python 3.12–3.14, shards it across
Windows, audits locked dependencies with `pip-audit`, and builds the wheel to verify it
ships the packaged data (query files, adapter snippets, and the managed skill). If you add packaged data, make sure it lands inside
`src/synapse/` so the wheel picks it up.

## Adding a language

Languages are data, not code. There is no per-language branching in the parser.

1. Add `src/synapse/queries/<lang>/symbols.scm` and
   `src/synapse/queries/<lang>/references.scm`.
2. Register the language in
   [src/synapse/core/languages/registry.py](src/synapse/core/languages/registry.py),
   mapping its captures onto Container / Entity / Worker. There is no separate grammar
   list — `synapse grammars install` derives the set from `LanguageSpec.tree_sitter_name`,
   so the grammar must be one `tree-sitter-language-pack` ships.
3. Add a sample tuple to the matching `TIER*_SAMPLES` table in
   `tests/core/indexing/test_parser_tier*.py` (or a dedicated test in `test_parser.py`
   for Tier-1 coverage), add the language to the corresponding `TIERn_LANGUAGES` list in
   `tests/core/languages/test_queries.py`, and add an extension line to
   `test_detect_language_by_extension` in `tests/core/languages/test_registry.py`.

If a language seems to need a special case in the parser, raise an issue first — that
usually means the model or the query needs adjusting instead.

## Adding an MCP tool

MCP tools are the agent-facing contract, so they are deterministic and token-frugal.

- Typed parameters and typed returns. Return structural data, not prose.
- The docstring *is* the contract: document parameter rules, the return shape, and how the
  tool differs from neighbouring ones.
- Keep the presentation layer thin. `mcp` may import `core`; `core` must never import
  `mcp`.
- Register it with `@tool()` (full profile) or `@tool(ToolProfile.DEFAULT, ...)` in
  `src/synapse/mcp/tools.py`; the wire name is the function name and there is no second
  list to edit. Bump the hard tool count asserted in `tests/mcp/test_profiles.py`.
- Add tests under `tests/mcp/` that exercise the tool as a thin delegator, and unit-test
  the underlying logic directly in `tests/core/`.
- Update the tool roster and the `(N tools total)` count in [AGENTS.md](AGENTS.md), the
  reference in [docs/tools.md](docs/tools.md), and the roster line in
  [docs/architecture.md](docs/architecture.md).

## Conventions

- English only, in code, comments, and docs.
- KISS, DRY, SOLID. Introduce an interface when a second implementation actually exists,
  not before.
- Match the surrounding comment density; no narration comments.
- Manage dependencies with `uv add` / `uv remove` rather than editing `pyproject.toml` by
  hand, and commit the resulting `uv.lock`.

## Commits and pull requests

- Small, focused commits with conventional prefixes: `feat:`, `fix:`, `docs:`,
  `refactor:`, `test:`, `chore:`.
- Never commit `.venv/`, `__pycache__/`, `.idea/`, or `.ai/` output.
- Add a `CHANGELOG.md` entry under `## [Unreleased]` for anything user-visible, and mark
  behavior changes that break existing usage with **Breaking:**.
- Fill in the pull request template, including the quality-gate checklist.

## Reporting bugs

Open an issue using the bug report form and include the `synapse doctor` and
`synapse watch status --json` output it asks for — most reports are diagnosed straight
from those two blocks. For security issues, follow [SECURITY.md](SECURITY.md) instead of
opening a public issue.
