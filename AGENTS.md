# AGENTS.md — Working on Synapse

Synapse is a local-first, AST-based code context engine exposed to AI agents over MCP.
This file is the contract for any human or AI agent contributing to THIS repository.

## Setup
- Python >=3.12. Use `uv`: `uv venv && uv pip install -e ".[dev]"`.
- Install parser binaries explicitly with `synapse grammars install`; indexing never
  downloads grammars implicitly.
- Configure an agent once with `synapse install <agent>`; initialize a workspace manually
  with `synapse init --path .` only for diagnostics.
- Run the MCP server directly for diagnostics: `python -m synapse serve --workspace .`.
- Remove global managed integration with `synapse uninstall <agent> --global`; this does not
  delete index/cache data.

## Project structure
- `src/synapse/core` — core logic: model, parsing, indexing, querying. No MCP imports.
  Grouped into cohesive sub-packages, each with a re-export `__init__.py` that is the
  package's public surface — import from the package, not its private submodules:
  - `core/models` — normalized symbol model.
  - `core/config` — layered config (`settings.py`) and the shared ignore matcher (`ignores.py`).
  - `core/languages` — the language seam: `registry.py`, `grammars.py`, `grammar_install.py`,
    `queries.py` (`.scm` loading).
  - `core/index` — SQLite index: `symbol_index.py` entry object over `schema.py`,
    `writes.py`, `reads.py`.
  - `core/indexing` — the pipeline: `crawler.py` → `parser.py` → `pipeline.py` → `references.py`.
  - `core/watch` — incremental watch daemon.
  - `core/workspace.py`, `core/lifecycle.py` — flat: a universal leaf and the top-level facade.
- `src/synapse/mcp` — FastMCP presentation layer; thin, delegates to `core`.
- `src/synapse/cli` — CLI entrypoints for indexing, setup, and MCP install helpers.
- `src/synapse/adapters/` — agent-specific metadata and instruction snippets (packaged data).
- `src/synapse/queries/<lang>/*.scm` — declarative tree-sitter queries (the language-agnostic seam, packaged data).
- `docs/architecture.md` — read this before changing structure.

## Architecture rules
- Keep core logic separate from presentation: `mcp` may import `core`; `core` never imports `mcp`.
- Start simple. Introduce interfaces/abstractions only when a real second implementation appears.
- New languages are added via `.scm` queries + a mapping to Container/Entity/Worker —
  NOT via per-language branching in the parser.

## Coding conventions
- English only (code, comments, docs). KISS, DRY, SOLID; no premature abstraction.
- Full type hints on every signature and dataclass. Prefer `@dataclass(slots=True)`/`StrEnum`.
- Format & lint with ruff; type-check with `mypy --strict`. No new errors.
- Match surrounding comment density; no narration comments.

## Testing
- `pytest`. Unit-test `core` logic directly; test MCP tools as thin delegators.
- Add tests in `tests/`, mirroring the package path. Cover edge cases.

## MCP tool conventions
- Optimize for the total tokens and model turns needed to complete a user task, not
  merely for the size of one tool response. Many individually compact, sequential
  tool calls can cost more context than one bounded, task-oriented response because
  every result remains in the agent history.
- Tools are deterministic, evidence-first, and token-frugal at both levels: each
  response must be concise, and a normal workflow must avoid unnecessary round trips,
  repeated metadata, and duplicated projections. Typed params/returns; concise
  docstrings (the docstring is the agent-facing contract). Return structural data, not
  prose.
- Keep low-level index operations reusable in `core`, but do not expose every helper
  as an MCP tool. For multi-file architecture, lifecycle, impact, or flow questions,
  prefer a bounded high-level context operation that performs search, traversal,
  ranking, deduplication, and projection server-side. A high-level MCP tool must call
  core APIs directly, never invoke other MCP tools.
- Every bounded or partial result must make its coverage explicit. Empty, paginated,
  truncated, ambiguous, heuristic, and unindexed results must never look like proof of
  absence or complete coverage.
- The MCP surface is profile-tiered (`synapse serve --profile default|full`; the
  registry lives in `mcp/profiles.py`). The default profile is the minimal coding-agent
  surface: `synapse_ensure_workspace`, `synapse_query_context`, `synapse_get_definition`,
  `synapse_get_symbol_context`, `synapse_find_references`. The full profile adds:
  `synapse_index_workspace`, `synapse_search_symbols`, `synapse_get_file_outline`,
  `synapse_get_dependencies`, `synapse_workspace_stats`, `synapse_project_map`,
  `synapse_get_file_dependencies`, `synapse_related_symbols`, `synapse_compact_context`,
  `synapse_watch_status`, `synapse_get_config`, `synapse_add_ignored_directories`,
  `synapse_remove_ignored_directories`.
- `synapse_query_context` is the bounded, task-oriented context operation: seed
  discovery, multi-hop traversal over stored relations, ranking, deduplication, and
  projection run server-side in `core/context`, under one deterministic output budget
  and one consistent read snapshot. It returns a single compact JSON string so the
  budget applies to the exact wire payload.

### Agent workflow

```
synapse_ensure_workspace()

synapse_query_context(question=...)
→ ranked flow with file:line evidence, confidence, and coverage

targeted definition, reference, or source check (at most a few)
→ verify the specific claim that still needs exact evidence
```

Start with one `synapse_query_context` call for architecture, lifecycle, impact, and
multi-file flow questions; narrow with `symbol_ids`, `direction`, or `max_depth` rather
than enumerating low-level tools. Stop once the task has enough evidence.

Avoid intermediate searches or manual `grep` when a `symbol_id` is available.
Use grep or whole-file reads only for exact-text verification, unsupported syntax,
generated files, or an explicit index-coverage gap. Do not repeat a successful Synapse
investigation as a full shell investigation merely for reassurance.

## Commits / PRs
- Do not commit `.venv/`, `__pycache__/`, `.idea/`, or `.ai/` output.
- Small, focused commits; conventional style (`feat:`, `fix:`, `docs:`, `refactor:`).

## Do NOT
- Upload code anywhere or add network calls in the index path (local-first guarantee).
- Edit `pyproject.toml` deps by hand — use `uv add` / `uv remove`.
