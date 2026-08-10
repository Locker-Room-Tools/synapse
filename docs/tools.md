# MCP Tools

Synapse exposes deterministic structural data from a local AST index. Agents should use these
tools before broad text search or reading whole files.

## Tool profiles

The advertised tool surface is selected at server start: `synapse serve --profile default`
(the default) exposes exactly the two navigation tools — `synapse_orient` and
`synapse_inspect` — which keeps the static `tools/list` schema cost minimal (measured at
roughly 550 estimated tokens for the whole surface, gated at 700). `--profile full`
additionally exposes `synapse_ensure_workspace` and the administrative, configuration,
and primitive projection tools documented below (19 tools total). `synapse doctor`
validates the default surface exactly.

## The navigation contract

Synapse supplies compact structural evidence; the agent supplies semantic interpretation,
query expansion, investigation planning, and synthesis. Neither tool accepts a
natural-language question: the agent first translates the task into likely repository
vocabulary (identifiers, file names, path fragments), and core evaluates those terms
literally. Both tools check readiness first and repair the workspace automatically when
needed — see [Workspace readiness](#workspace-readiness) for exactly what "ready" means.

Every response distinguishes `payload_complete` (whether the serialized projection fit
its caps and budget) from evidence coverage: `coverage.scope` names the bounded scope
(`ranked-orientation` or `selected-symbol-one-hop`), and discovered/returned/omitted
counts, fixed caps, missing handles, source state, and per-language extraction
completeness stay visible. Synapse never emits task completeness — a complete payload is
not a claim that the evidence or the final answer is complete.

Symbols are addressed by deterministic compact handles: `s_` plus 22 base64url characters
derived from a 128-bit digest of the internal stable ID. Handles are stored under a
unique index (a collision fails indexing explicitly and can never alias another symbol);
stable IDs remain canonical internally and remain visible through full-profile
primitives, which accept them interchangeably.

### `synapse_orient`

The first call for any code question: ranked, production-first orientation.

- Parameters: `terms=None` (4–8 discriminative repository terms normally, 12 maximum;
  empty explicitly requests repository-map orientation), `path_scope=None` (restrict
  to a workspace-relative path prefix), `workspace_path="."`. The response is bounded
  at the server default of 800 estimated tokens; the MCP surface exposes no budget
  override (`OrientRequest.token_budget` remains configurable for core callers).
- Ranking signals: exact-name, name-prefix/substring (at word starts, snake and
  camel), literal path (exact, `/`-suffix, and — for path-shaped terms — substring),
  trusted centrality (exact/scoped incoming references, bucketed), entrypoint and
  repository-map anchors. A simple crowd penalty keeps generic terms from
  dominating: a term whose workspace-wide declaration-match count exceeds
  `max(25, symbols/100)` is reported in `crowded_terms` with its count, and its
  non-exact candidates surface only as weak candidates. There is no intent,
  ontology, cluster, facet, or paraphrase inference.
- Name and path are **separate retrieval channels**. The name channel matches
  declaration names and qualified names only; it never matches file paths, so a
  large file whose path contains the term cannot fill the bounded page and hide
  real name matches. Literal path matching is its own channel.
- Every narrowing binds **in the query, before the page bound** — the scope, and the
  exclusion of import statements, which are not declarations. Out-of-scope rows and
  imports therefore cannot consume a page and hide a real in-scope declaration. A path
  is in scope when it is the scope itself or lies under it, so `app` does not match
  `application/`. (The full-profile `synapse_search_symbols` is a different contract and
  still returns imports, including for an explicit `kind="import"`.)
- The crowd metric measures both sides against the same population: name matches among
  **searchable declarations inside the scope**, over the count of those declarations,
  keeping the `max(25, population / 100)` rule. A term that is generic workspace-wide but
  rare inside the scope is not demoted, and one that saturates a small scope inside a
  large workspace is correctly reported as crowded.
- A path term's shape decides how it matches, and retrieval, counting, and acceptance all
  use that one rule: a **path-shaped** term (containing `/` or `.`) matches an exact path,
  a `/`-suffix, or a substring; a **bare** word matches only an exact path or a whole
  trailing path component. So `handler` does not match `pkg/handler_noise_00.py`, and
  `files_omitted` can never contradict `unmatched_terms`.
- Response: a deduplicated `files` table (entries reference it by index — every
  row is referenced by some entry), ranked `matches` (compact handle `h`, name
  `n`, kind `k`, file index `f`, line `l`, match provenance `m`:
  `exact|prefix|substring|path|map`, matched term `t`, bucketed trusted in-degree
  `in`, entrypoint/anchor flag `ep`), `weak` candidates/hypotheses,
  `file_matches` (bounded literal path hits: file index `f`, matching term `t`,
  indexed declaration count `d` — `d: 0` is an explicit "matched file, nothing
  declared in it"), `crowded_terms`, `unmatched_terms` (never silently dropped),
  a `map` section for empty-terms orientation (bounded areas, entrypoints, and
  `bridges`), `coverage`, `payload_complete`, and the `budget` block.
- `map.bridges` are the trusted cross-area links from the repository map, using
  compact area indexes into `map.areas`: `a`/`b` (area indexes), `r` (exact/scoped
  cross-area reference count), `i` (import count), and at most one `path:line`
  example `x`. Under budget pressure the examples drop first, then whole bridges;
  areas and anchors are kept longer because they are what an agent addresses.
- `coverage.caps` names the fixed bounds that shaped the answer (`names`, `paths`,
  `matches`, `weak`, `files`), with `name_omitted` and `files_omitted` when the
  caps actually removed something. `files_omitted` counts every distinct matching
  file, not just the ones the path limit retrieved, and `path_capped: true` marks the
  case where matches were never retrieved at all — distinct from budget truncation,
  which `budget.dropped` accounts for.
- Production ranks before tests and generated code — for both symbol matches and
  file matches — unless tests are explicitly scoped with `path_scope`.

### `synapse_inspect`

One batch inspection of the selected symbols under one read snapshot.

- Parameters: `symbols` (1–8 compact handles or internal stable IDs; normally 2–3
  initial facet-diverse anchors, with follow-ups reusing relation handles),
  `workspace_path="."`. The response is bounded
  at the server default of 2400 estimated tokens; the MCP surface exposes no budget
  override (`InspectRequest.token_budget` remains configurable for core callers).
- Per selected symbol: the definition (qualified name, kind, `file` index,
  `lines`, signature), a bounded source slice (`src`, at most 40 lines, with
  `truncated` and `shortened` flags), `parent` and `children` (capped at 12 with
  `children_total`), and four relation groups — `callers`, `callees`, `refs_in`,
  `refs_out` — at most 12 incoming and 12 outgoing groups (call and non-call
  groups share that bound), each group carrying its endpoint definition and up to
  3 sites (`more` counts the rest), every site preserving stored resolution
  (`exact|scoped|unique-name|ambiguous|unresolved`), confidence, usage kind, and
  location verbatim — plus unresolved incoming `hypotheses` by name with
  `hyp_total`. `in_total`, `out_total`, and omitted counts stay visible.
- Unknown handles or IDs are listed in `missing`, never guessed. Outgoing
  references whose target is unresolved keep their target name and stored
  resolution instead of a handle.

#### Call semantics

`callers` and `callees` contain **call-proven sites only**. A call is never inferred
from either endpoint's declaration kind: a site counts as a call only when its stored
`usage_kind` is one the language advertises as proving that control transfers into the
target. Today that is `invocation` and `object-creation` for C#, TypeScript, TSX, and
JavaScript — `new Repo()` is a constructor invocation — and `invocation` for Python.

Everything else is returned as **neutral evidence** in `refs_in`/`refs_out` with its
usage kind verbatim. A C# `declared-type` reference (`void M(Repo repo)`) therefore never
makes `M` a caller of `Repo`, and a member read (`repo.Total`) is never a call even
though it shares the member-access syntax with one. Python base lists and bare decorator
names are likewise neutral.

If one endpoint carries both call and non-call sites, it is split into two groups; the
non-call sites are never upgraded to join the call group.

A language that advertises no call usage kinds produces **no callers or callees at all**,
and all of its evidence lands in `refs_in`/`refs_out`. Read
`coverage.extraction[].call_kinds`: an empty list means an empty `callers` proves
nothing about that language.

#### Coverage

`coverage` reports what the returned evidence is made of:

- `resolution_model: "syntactic-structural"` and `exhaustive: false`
- `extraction` — call and extraction calibration per language, each with `completeness`
  (`partial` by default, so an empty `limitations` list can never read as complete),
  `call_kinds`, and `limitations`. Bounded, with `extraction_omitted`.

  It normally covers the languages that actually produced the returned evidence — the
  selected symbols' languages **and** the languages of the returned relation sites. When
  a selected symbol has **zero incoming references** it also covers the remaining
  workspace languages, marked `"evidence": false`, because a caller could have been
  written in any of them and an empty caller set is only readable against their call
  coverage. Evidence-producing languages omit the key, so a normal payload is unchanged.
- `hypotheses_total` / `hypotheses_omitted` — exact, not a truncated list
- source state, one cause per field and never conflated:
  - `source_truncated` — the definition outgrew the fixed 40-line slice cap
  - `source_shortened` — the wire budget removed lines the fixed slice would have held
  - `source_omitted` — the budget removed the slice entirely
  - `source_unavailable` — the indexed location cannot be read

  A body that outgrew the cap *and* was then shortened by the budget appears in both of
  the first two: two real causes, not a conflation. Entry-level `src.truncated` means
  something deliberately different — *the text shown here is incomplete*, whatever the
  cause — because that is what an agent reading the slice needs; `src.shortened` and the
  coverage fields say which cause applied.
- `relations_returned` / `relations_omitted`, `selected` / `requested`

Both tools guarantee output size the same way: the applied `token_budget` is an
**estimate** at a fixed 4 characters per token; the hard, tested guarantee is that the
returned string never exceeds the accepted budget times 4 **characters** and is always
valid JSON. The MCP tools always apply the core defaults — an agent cannot raise the
budget, so a gap is closed with a narrower targeted call rather than a bigger payload.
Normal budget pressure drops low-priority content deterministically (hypotheses and
weak evidence first, then caller/callee groups shrink to a compact navigation set —
keeping at least one relation group per direction when evidence exists — before any
selected symbol's source is removed; the first-requested symbol degrades last); under
extreme pressure the result shrinks to a minimal envelope and, as a last resort, a
fixed truncation-only envelope that always carries `complete: false`. Empty or truncated results are never
proof of absence — read `coverage`.

The intended workflow: translate the request into repository vocabulary →
`synapse_orient` → `synapse_inspect` with 2-3 facet-diverse anchors → follow 1-2
returned relation handles for facets still open → synthesize with the model. Two calls
remain the common fast path, not a cap: a weakly matched orientation may need one more
`synapse_orient`, and a follow-up `synapse_inspect` may reuse relation handles from a
previous inspection. Use exact-text search or file reads only for gaps the coverage
block reports.

## Workspace readiness

Ready means **queryable and semantically current**, not merely "a daemon is running".
Before answering, a navigation call repairs the workspace when any of these holds:

| reason | meaning |
|---|---|
| `no-index` | metadata exists but the SQLite index does not |
| `not-ready` | uninitialized, initializing, or a degraded/dead daemon |
| `missing-grammars` | a supported parser is not installed locally |
| `stale-references` | stored relations were produced under older extraction semantics |

The last one is why daemon health alone is insufficient: the reference fingerprint covers
the schema version, the extractor version, and every packaged `.scm` query, so a
workspace can migrate its SQLite schema — which happens on any index construction — while
keeping relations built by an older extractor. Without this check it would serve that
stale evidence indefinitely.

The probe is read-only and never allocates the cache directory, because it runs while a
watch daemon or a concurrent rebuild may own the database. A healthy, current workspace
performs no ensure and no re-index. A repair goes through `ensure_workspace`, which owns
grammar installation, the daemon stop, the watch lock, and the atomic rebuild; readiness
is re-verified afterwards before the call proceeds. If another process wins the repair
race, the call waits for that rebuild rather than surfacing a lock error.

### `synapse_ensure_workspace` (full profile)

Explicit initialization/repair for setup, diagnostics, and recovery; the navigation tools
do this lazily on their own, so it is not part of the normal workflow.

- Parameters: `workspace_path="."`
- Returns: `workspace_path`, `action` (`initialized`, `reused`, or `repaired`),
  `initialized`, daemon health, and compact index counts
- Installs missing grammars, initializes or updates the index, and ensures a healthy daemon

Full-profile query tools reject uninitialized or degraded workspaces with an instruction
to initialize first. This prevents an empty SQLite database from appearing to be a valid
result.

## Canonical navigation (full profile)

Resolve a declaration first, then reuse its stable identifier:

```text
synapse_get_definition(name="synapse_find_references")
→ {symbol_id, handle, file_path, line_range, ...}

synapse_find_references(symbol_id="...")
→ {items: [...], files: [...], page: {...}}
```

Symbol summaries include both the internal `symbol_id` and the compact `handle`, so
full-profile results interoperate with `synapse_inspect`. Use `synapse_search_symbols`
only when the exact declaration name is unknown or multiple candidates need filtering.
Fall back to grep or file reads when a symbol is not indexed or exact source text is
required.

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

Ignore rules resolve as three **ordered** layers — packaged defaults, the global layer
(`~/.config/synapse/ignore`), and the project layer (`<workspace>/.synapseignore`) — and the
**last matching rule wins**. A later layer can re-include what an earlier one ignored. The MCP
tools write the **project** layer only, so an ignore added for one repository never leaks into
another. `.synapseignore` is meant to be committed.

Patterns use gitignore syntax:

| Form | Example | Matches |
| --- | --- | --- |
| bare name | `node_modules` | that name at any depth, file or directory |
| directory only | `build/` | directories named `build` at any depth, never a file |
| root-anchored | `/dist` | only `<workspace>/dist` |
| workspace-relative path | `src/generated/` | only `<workspace>/src/generated/` |
| glob | `*.min.js`, `test_?.py`, `[Bb]uild/`, `docs/**` | matching names |
| negation | `!src/vendor/keep.js` | re-includes what an earlier rule ignored |

Absolute paths, `.`/`..` segments, and empty strings are rejected. Matching is case-sensitive
because patterns are compared against real path names. `.git` is always ignored and cannot be
re-included.

A path is decided component by component from the root down, as git does it: the first
component that resolves to ignored wins, so a negation beneath an ignored directory is inert
(`!build/keep.py` does nothing when `build/` is ignored).

Anchoring is relative to the resolved `workspace_path`. Passing a subdirectory as
`workspace_path` therefore selects a different, separately-anchored ignore file; every payload
echoes the resolved `workspace_path` so this stays visible.

If a layer still has a legacy `ignored_directories` list in its `config.json`, an ignore file
supersedes it; the superseded entries are reported in `shadowed_project_json`. The first write
adopts the ignore file and migrates those entries into it, reported as `migrated_from_json`.

### `synapse_get_config`

Ordered ignore rules with per-rule provenance and write targets. Safe before initialization.

- Parameters: `workspace_path="."`
- Returns: `workspace_path`, `project_config_path`, `project_config_exists`,
  `global_config_path`, `watch_poll_interval_s`, and an `options` map whose `ignore_rules`
  carries `type`, `semantics`, `project_source`, `project_ignore_file`, `global_ignore_file`,
  `accepted_forms`, `rejected`, `case_sensitive`, `always_ignored`, `writes_to`, `layers`,
  `takes_effect`, `rules` (each `{pattern, scope, origin, line, negated, directory_only}` in
  evaluation order), `rules_total`, `rules_complete`, `skipped_lines`,
  `shadowed_project_json`, and `coverage`

`rules` is bounded at 200 entries; `rules_total` and `rules_complete` make that explicit. This
is the **rule list, not the set of ignored paths** — with negation, whether a path is ignored
depends on rule order, so no flat effective set exists.

### `synapse_add_ignored_directories`

Stop indexing paths by appending gitignore patterns to `.synapseignore`.

- Parameters: `directories` (list of patterns), `workspace_path="."`
- Returns: `scope`, `config_path`, `created`, `added`, `already_present`, `negated`,
  `not_present`, `migrated_from_json`, `normalized`, `project_rules`, `takes_effect`,
  `coverage`

Patterns append to the end, so a newly added rule beats the rules already there. The file is
created (and any legacy entries migrated into it) when it does not exist. Any invalid entry
rejects the whole call and writes nothing.

### `synapse_remove_ignored_directories`

Resume indexing paths by editing `.synapseignore`.

- Parameters: `directories` (list of patterns), `workspace_path="."`
- Returns: the same fields as `synapse_add_ignored_directories`

A pattern the project file owns is deleted and reported in `removed`. A pattern inherited from
a built-in or the global layer cannot be deleted there, so a negation is appended instead and
reported in `negated` — that is how a built-in gets turned off. `.git` is the exception and
stays ignored. A pattern that is not ignored anywhere is reported in `not_present` and is not
an error.

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
