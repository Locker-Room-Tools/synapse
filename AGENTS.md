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
  - `core/config` — ordered ignore layers (`settings.py`), the shared gitignore-style matcher
    (`ignores.py`), and the ecosystem templates plus first-run bootstrap (`ignore_presets.py`).
  - `core/languages` — the language seam: `registry.py`, `grammars.py`, `grammar_install.py`,
    `queries.py` (`.scm` loading).
  - `core/index` — SQLite index: `symbol_index.py` entry object over `schema.py`,
    `writes.py`, `reads.py`; `handles.py` (compact wire handles), `source.py`
    (bounded source slices), `repo_map.py` (the materialized repository map).
  - `core/navigation` — the two-call navigation contract: `orient.py`,
    `inspection.py`, `matching.py`, `traversal.py`, `render.py`, `budget.py`.
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
  registry lives in `mcp/profiles.py`). The default profile is exactly the two-call
  navigation surface: `synapse_orient`, `synapse_inspect` — both initialize the
  workspace lazily. The full profile adds: `synapse_ensure_workspace`,
  `synapse_index_workspace`, `synapse_search_symbols`, `synapse_get_definition`,
  `synapse_get_file_outline`, `synapse_get_dependencies`, `synapse_workspace_stats`,
  `synapse_project_map`, `synapse_get_file_dependencies`, `synapse_get_symbol_context`,
  `synapse_find_references`, `synapse_related_symbols`, `synapse_compact_context`,
  `synapse_watch_status`, `synapse_get_config`, `synapse_add_ignored_directories`,
  `synapse_remove_ignored_directories` (19 tools total).
- The navigation contract (`core/navigation`): Synapse supplies compact structural
  evidence; the agent supplies semantic interpretation, query expansion, planning,
  and synthesis. `synapse_orient` evaluates up to 12 agent-chosen repository terms
  literally (exact-name, prefix/substring, literal path, trusted centrality,
  entrypoint, repository map, plus a simple crowd penalty — no intent or cluster
  inference) and returns ranked production-first matches with compact handles
  (`s_` + 22 base64url chars of the stable-ID digest). Name and path retrieval are
  separate channels, and matched files are returned explicitly (including files with
  no declarations). `synapse_inspect` resolves 1–8 handles or stable IDs under one
  read snapshot and returns definitions, bounded source slices, parents/children,
  and four relation groups — `callers`, `callees`, `refs_in`, `refs_out` — with
  stored resolution, confidence, and usage kind verbatim. Both return one
  compact JSON string so the budget binds the exact wire payload (orient: 800
  estimated tokens, inspect: 2400), always report `payload_complete` and bounded
  `coverage`, and never claim task completeness. The MCP tools expose no
  `token_budget` parameter — the core requests stay configurable, but an agent
  cannot enlarge a navigation payload, so gaps are closed with narrower calls.
- Call semantics are evidence-based. `callers`/`callees` hold only sites whose stored
  usage kind proves a call (`LanguageSpec.call_usage_kinds`; C#, TypeScript, TSX, and
  JavaScript: `invocation`, `object-creation`; Python: `invocation`). A call is never
  inferred from either
  endpoint's declaration kind, so a `declared-type` or `base-type` reference is
  neutral `refs_in`/`refs_out`, and a language advertising no call kinds returns no
  callers or callees at all — `coverage.extraction[].call_kinds` says so.
- Navigation readiness lives entirely in `core.lifecycle`
  (`navigation_repair_reason`, `ensure_navigation_ready`): no index, not ready, a watch
  daemon whose writer contract does not match this runtime, missing grammars, a stale
  reference fingerprint, or incomplete persisted handles all force a repair before the
  call answers. The probe is read-only; `mcp` only delegates.
- Handles round-trip or navigation fails. Orientation renders a handle from the stable
  id while inspection resolves the persisted `symbols.handle`, so completeness is an
  invariant, not a detail: the column is `NOT NULL` and shape-checked in the schema,
  `INDEX_WRITER_CONTRACT_VERSION` (`core/index/contract.py`) identifies the persistence
  contract a watch daemon implements, and a daemon with missing or mismatched
  provenance is stopped before any repair touches the database. Bump the writer
  contract whenever symbol-write invariants change — the package version is not enough,
  since two development builds share one version.

### Agent workflow

```
list the evidence facets the task needs, then translate them into repository
vocabulary (identifiers, files, paths)

synapse_orient(terms=[...])            # 4–8 discriminative terms, 12 maximum
→ ranked production-first matches with compact handles, weak candidates,
  explicit file matches, crowded/unmatched terms, and coverage
  (no terms → repository map with areas, entrypoints, and bridges)

synapse_inspect(symbols=[...])         # 2–3 initial facet-diverse anchors
→ definitions, bounded source, call-proven callers/callees plus neutral
  refs_in/refs_out, all with stored resolution, confidence, and usage kind;
  follow 1–2 returned relation handles for facets still open

mark each facet verified / partial / missing; give a partial/missing facet one
bounded close attempt, then report it verified or unresolved
```

Two calls remain the common fast path, not a cap; a weakly matched orientation may need
one more `synapse_orient` with better terms or a `path_scope`, and a follow-up
`synapse_inspect` may reuse relation handles from a previous inspection. Stop once every
requested facet is verified or explicitly reported unresolved. The canonical detailed
workflow is the managed skill `src/synapse/skills/synapse-code-context/SKILL.md`; the
server handshake and adapter snippets are deliberately short pointers to it.

Avoid intermediate searches or manual `grep` when a handle or `symbol_id` is available.
Use grep or whole-file reads only for exact-text verification, unsupported syntax,
generated files, or an explicit index-coverage gap. Do not repeat a successful Synapse
investigation as a full shell investigation merely for reassurance.

## Commits / PRs
- Do not commit `.venv/`, `__pycache__/`, `.idea/`, or `.ai/` output.
- Small, focused commits; conventional style (`feat:`, `fix:`, `docs:`, `refactor:`).

## Do NOT
- Upload code anywhere or add network calls in the index path (local-first guarantee).
- Edit `pyproject.toml` deps by hand — use `uv add` / `uv remove`.
