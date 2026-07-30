# MCP Tools

Synapse exposes deterministic structural data from a local AST index. Agents should use these
tools before broad text search or reading whole files.

## Tool profiles

The advertised tool surface is selected at server start: `synapse serve --profile default`
(the default) exposes the minimal coding-agent set — `synapse_ensure_workspace`,
`synapse_query_context`, `synapse_get_definition`, `synapse_get_symbol_context`, and
`synapse_find_references` — which keeps the static `tools/list` schema cost low.
`--profile full` additionally exposes the administrative, configuration, and primitive
projection tools documented below. `synapse doctor` validates the default surface exactly.

## High-level context query

### `synapse_query_context`

The first call for architecture, lifecycle, impact, and multi-file execution-flow
questions. One bounded call performs seed discovery, multi-hop traversal over stored
relations, ranking, deduplication, and projection server-side.

- Parameters: `question`, `symbol_ids=None`, `direction="both"` (`in`/`out`/`both`),
  `max_depth=3` (clamped 1–5), `token_budget=4000` (clamped 500–20000),
  `include_source=False`, `workspace_path="."`
- Traversal trust policy: only containment and `exact`/`scoped` references are
  **transit** edges. Heuristic references (`unique-name` or unclassified) are recorded
  as **leaf evidence** — the edge and its far symbol appear with full resolution and
  confidence metadata, but traversal never continues past them, so unrelated symbols
  cannot be chained through a shared name. Suppressed heuristic expansion is counted
  in `coverage.traversal.not_expanded_heuristic`.
- Seed origin: seeds come from explicit `symbol_ids` (`explicit-symbol`), question
  matching (`question-match`), or — when the question matches no symbol at all — a
  bounded, deterministic **structural fallback** (`structural-fallback`).
  `coverage.seeds` reports the origin, the `fallback_reason`, and
  `only_test_matches: true` when every seed lives in test code.
- Seed tiering: an exact production declaration for a token dominates — prefix and
  term matches for that token stay visible as alternates but never become peer
  active seeds. Multiple exact declarations (overloads, genuine ambiguity) all stay
  active; exact test-only matches stay active but flagged. Long unmatched terms
  (≥7 chars) retry once with a 6-char prefix, so morphological variants
  ("registration") still reach declarations ("register_...").
- Structural fallback ranking (documented trust semantics): a deterministic integer
  score over 3× incoming **exact/scoped** references (heuristic unique-name
  popularity never counts), 2× containment children, 4× file import reach (distinct
  files whose declared import names the symbol's module), +2 public name (no
  leading underscore), +2 shallow production path — then kind relevance and path
  depth; alphabetical order is only the final tiebreak. Test paths are excluded and
  a per-directory cap spreads seeds across repository areas.
- Projection policy (`coverage.projection.policy`): queries whose seeds include
  test code run `test-relevant` (full test evidence). Everything else runs
  `production-focus`: projected test nodes are capped at 5, excess is demoted (and
  counted in `coverage.projection.tests = {discovered, projected, demoted}`), and
  budget drops remove test evidence before comparable production evidence. Test
  evidence is never globally removed — impact queries still surface tests within
  the cap.
- Returns: one compact JSON **string** (a single wire representation; no duplicated
  structured payload) with:
  - `seeds` — ranked seed symbols with match provenance (`explicit`/`exact-name`/
    `prefix`/`term`/`structural`) and a `test_path` marker for test code;
  - `alternates` — ambiguity is explicit: further candidates with the total count;
  - `nodes` — discovered symbols with `depth` and a `via` edge (stored kind,
    direction, resolution, confidence, `file:line` site, usage kind) — stored facts,
    verbatim;
  - `flows` — `{ids, trust}` root-to-leaf chains projected over the BFS discovery
    tree; only verified chains project: every hop is exact or scoped, so `trust` is
    `exact` or `scoped` (a heuristic unique-name relation is a hypothesis and stays
    node/edge evidence, never a flow). Flows are ranked by aggregate path trust,
    then question relevance, then depth. When no chain qualifies the section is
    omitted and `coverage.projection.flows_omitted` says why: `no-trusted-flow`
    (substantive chains existed but all were heuristic) or `no-relevant-flow`
    (no chain carried a reference edge or question-relevant leaf);
  - `edges` — a bounded, ranked projection of discovered **non-tree** edges
    (cross-links and cycles) with the same evidence fields;
  - `unresolved` — seed-level references the index could not bind to one target
    (`ambiguous`/`unresolved`), with name and `file:line` site, so gaps invite a
    targeted `synapse_get_definition` drill-down instead of looking like absence;
  - `imports` — file-level import facts for discovered files;
  - `coverage` — index freshness/staleness, seed origin, per-language extraction
    completeness and limitations, traversal guards (depth/nodes/edges/fan-out
    suppression, remaining frontier, suppressed heuristic expansion), resolution
    counts, and `projection.edges` accounting (`discovered` / `tree_projected` /
    `extra_projected` / `omitted`) so returned edges are never mistaken for all
    discovered edges;
  - `truncation` — the applied budget, estimated tokens, and exactly what was dropped.
- Output-size guarantee: `token_budget` is an **estimate** at a fixed 4 characters per
  token; the hard, tested guarantee is that the returned string never exceeds the
  **clamped** budget (500–20000) times 4 **characters** and is always valid JSON —
  `query.token_budget` echoes the clamped value the cap applies to. Normal budget
  pressure drops low-priority content first and preserves seeds, the primary flow,
  and coverage while they fit; under extreme pressure the result shrinks to a
  minimal envelope (which may trim seeds) and, as a last resort, a fixed
  truncation-only envelope that always carries `complete: false`, so truncation can
  never look like a complete result. Empty or truncated results are never proof of
  absence — read `coverage`.

The intended workflow is `synapse_ensure_workspace` → one `synapse_query_context` → at
most a few targeted `synapse_get_definition` / `synapse_find_references` /
`synapse_get_symbol_context` checks.

## Workspace bootstrap

### `synapse_ensure_workspace`

Call this before the first code-navigation operation in a workspace.

- Parameters: `workspace_path="."`
- Returns: `workspace_path`, `action` (`initialized`, `reused`, or `repaired`),
  `initialized`, daemon health, and compact index counts
- Installs missing grammars, initializes or updates the index, and ensures a healthy daemon

All query tools reject uninitialized or degraded workspaces with an instruction to call this
tool. This prevents an empty SQLite database from appearing to be a valid result.

## Canonical navigation

Resolve a declaration first, then reuse its stable identifier:

```text
synapse_get_definition(name="synapse_find_references")
→ {symbol_id, file_path, line_range, ...}

synapse_find_references(symbol_id="...")
→ {items: [...], files: [...], page: {...}}
```

Use `synapse_search_symbols` only when the exact declaration name is unknown or multiple
candidates need filtering. Fall back to grep or file reads when a symbol is not indexed or
exact source text is required.

## Discovery and workspace overview

### `synapse_search_symbols`

Primary symbol lookup.

- Parameters: `query`, optional `kind`, optional `language`, `limit=20`, `offset=0`,
  `workspace_path="."`
- Returns: `items` containing compact symbol records and `page` metadata

### `synapse_project_map`

Compact workspace structure and high-value symbols.

- Parameters: `limit=50`, `offset=0`, `top_symbols_limit=20`, `workspace_path="."`
- Returns:
  - `tree` and `page` — one page of indexed files; `page.files` lists that page's
    paths. `limit`/`offset` page files only
  - `top_symbols` (+ `top_symbols_total`, `top_symbols_truncated`) — type and
    callable declarations only; namespaces and imports never fill slots. Ranked by
    kind relevance with a per-kind cap, so a class-heavy repository still surfaces
    records, enums, and methods rather than only classes
  - `namespaces` — `items` deduplicated and sorted, plus `total` (distinct names,
    independent of the item limit) and `truncated`
- `top_symbols` and `namespaces` are workspace-wide aggregates and repeat unchanged
  on every file page

### `synapse_workspace_stats`

Indexed file count, symbol count, and language mix.

- Parameters: `workspace_path="."`
- Returns: workspace statistics object

## Definitions and structure

### `synapse_get_definition`

Returns a declaration by `symbol_id` or exact `name`.

- Parameters: optional `symbol_id`, optional `name`, `limit=50`, `offset=0`,
  `workspace_path="."`
- Returns: one symbol, a paged `candidates` object for ambiguous names, or `null`
- At least one of `symbol_id` and `name` is required

### `synapse_get_file_outline`

Structural outline to call before opening a whole file.

- Parameters: `file_path`, `max_symbols=200`, `workspace_path="."`
- Returns: file metadata and nested symbols (each with kind, name, signature, and
  line range), or `null` when the file is not indexed

### `synapse_get_symbol_context`

Structural context around one symbol.

- Parameters: `symbol_id`, `include_body=false`, `children_limit=50`,
  `children_offset=0`, `max_body_lines=200`, `workspace_path="."`
- Returns: the symbol, parent/children context, and optional body, or `null`
- With `include_body=true` the body is the symbol's source, capped at
  `max_body_lines`; `body_truncated` reports when the cap cut the text

### `synapse_compact_context`

Minimum useful context for understanding one symbol.

- Parameters: `symbol_id`, `workspace_path="."`
- Returns: compact definition and relation context, or `null`

## References and dependencies

### `synapse_find_references`

Finds usages across the workspace. Resolution is syntactic and name-based; results
distinguish confirmed matches from same-name possibilities instead of merging them.

- Parameters: optional `symbol_id`, optional `name`, `limit=50`, `offset=0`,
  `workspace_path="."`
- Returns:
  - `items` — relations bound to the target by a unique-name heuristic
    (`match: "heuristic"`), each with `line` and `byte_column`
  - `possible_items` (+ `possible_total`) — same-name relations whose target is
    ambiguous or unresolved; ambiguous entries carry `candidate_symbol_ids`
    (capped), `candidate_count`, and `candidates_truncated`, and must never be
    read as confirmed usages
  - `coverage` — `resolution_model`, `exhaustive: false`, per-language
    `extraction` (completeness, usage kinds, limitation ids), match-kind
    `counts`, and `zero_result: "no-indexed-matches"` on empty answers
  - `files` — every affected path across the whole result, not just this page
  - `page` (confirmed) and `possible_page` (ambiguous/unresolved) — both honour
    the same `limit`/`offset`, so ambiguous results past the first page are
    reachable; `page.files` is the page-scoped path list
- Both collections are ordered by `from_file_path`, then line, then byte column,
  then relation id, so paging is stable and duplicate-free
- `coverage.counts`, `possible_total`, and `candidate_count` always describe the
  full result; only serialized lists are capped
- Prefer `symbol_id`; use `name` only when a stable identifier is unavailable
- An empty result means no references are indexed under partial coverage — it is
  not proof the symbol is unused

### `synapse_get_dependencies`

Outgoing symbol relations.

- Parameters: `symbol_id`, `limit=50`, `offset=0`, `workspace_path="."`
- Returns: relation `items` and `page` metadata

### `synapse_get_file_dependencies`

File-level imports and dependencies.

- Parameters: `file_path`, `limit=50`, `offset=0`, `workspace_path="."`
- Returns: file dependency data or `null`

### `synapse_related_symbols`

Graph-like neighbors around a symbol.

- Parameters: `symbol_id`, `limit=20`, `offset=0`, `workspace_path="."`
- Returns: related symbols and paging data, or `null`

## Index health and maintenance

### `synapse_watch_status`

Read-only daemon freshness and health.

- Parameters: `workspace_path="."`
- Returns: `running`, `backend`, `degraded`, `pending`, PID, timestamps, recent errors,
  `staleness_seconds`, `initialized`, and the resolved `workspace_path`

For a new workspace, MCP starts in bootstrap mode without a daemon. For an initialized
workspace, MCP startup and `synapse_ensure_workspace` enforce daemon health.

### `synapse_index_workspace`

Explicit incremental or forced indexing.

- Parameters: `workspace_path="."`, `force=false`
- Returns: compact indexing statistics

This is a recovery and administration tool, not the first step in normal navigation. A forced
rebuild is rejected while a live watcher owns the workspace.

## Configuration

Configuration resolves as a union of three layers: packaged defaults, the global user config
(`~/.config/synapse/config.json`), and the project config (`<workspace>/.synapse/config.json`).
The MCP tools write the **project** layer only, so an ignore added for one repository never
leaks into another. The project config is meant to be committed.

An ignored directory entry takes one of three forms:

| Form | Example | Matches |
| --- | --- | --- |
| bare name | `node_modules` | a directory of that name at any depth |
| root-anchored name | `/build` | only the top-level `build/` |
| workspace-relative path | `src/generated` | only `<workspace>/src/generated/` |

Absolute paths, `.`/`..` segments, and glob patterns are rejected. Matching is case-sensitive
because entries are compared against real directory names.

Anchoring is relative to the resolved `workspace_path`. Passing a subdirectory as
`workspace_path` therefore selects a different, separately-anchored project config; every
payload echoes the resolved `workspace_path` so this stays visible.

### `synapse_get_config`

Effective configuration with per-entry provenance and write targets. Safe before
initialization.

- Parameters: `workspace_path="."`
- Returns: `workspace_path`, `project_config_path`, `project_config_exists`,
  `global_config_path`, `watch_poll_interval_s`, and an `options` map. Each option carries its
  type, `accepted_forms`, `rejected` forms, `writes_to`, `layers`, `takes_effect`, and an
  `effective` list of `{value, sources}` where `sources` is any of `built-in`, `global`,
  `project`

### `synapse_add_ignored_directories`

Stop indexing directories by writing the project config.

- Parameters: `directories` (list), `workspace_path="."`
- Returns: `added`, `already_present`, `already_covered_by_builtin`, `normalized`,
  `project_ignored_directories`, `effective_ignored_directories`, `takes_effect`

Built-in ignores are reported as already covered rather than duplicated. Any invalid entry
rejects the whole call and writes nothing.

### `synapse_remove_ignored_directories`

Resume indexing directories by removing them from the project config.

- Parameters: `directories` (list), `workspace_path="."`
- Returns: `removed`, `not_present`, `normalized`, `project_ignored_directories`,
  `effective_ignored_directories`, `takes_effect`

Built-in ignores and entries inherited from the global config cannot be removed here; both
raise an error naming where the entry comes from and how to remove it. An entry that is not
ignored anywhere is reported in `not_present` and is not an error.

### Applying a change

No reindex is required. Ignored files leave the index and restored files re-enter it on the
next watch sweep (at most `watch_poll_interval_s`). Call `synapse_index_workspace` to apply
the change immediately.

## Pagination

Paged tools accept `limit` and `offset` and return a `page` object containing the total and
continuation metadata. Preserve the same filters and workspace path while advancing `offset`.
Prefer focused queries and small pages to keep agent context compact.

## Workspace paths

The global MCP integration resolves the nearest Git root from the agent process directory.
Most calls should leave `workspace_path="."`. Project-scoped configs continue to pin one
absolute workspace, and explicit absolute paths remain available for advanced multi-workspace
use.
