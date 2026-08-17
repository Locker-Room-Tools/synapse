# Synapse Architecture

## Problem and solution summary

AI agents need accurate code context, but raw-text retrieval is token-heavy and can expose
more intellectual property than necessary. Synapse is a local-first structural context
engine: it parses code into symbols and relationships locally, stores an index on disk, and
exposes concise structural answers through MCP.

## Normalized symbol model

Synapse normalizes language-specific syntax into an idiomatic, language-agnostic set of
symbol kinds such as namespace, package, module, class, interface, struct, record, enum,
type, function, method, constructor, property, field, variable, constant, and import.

Each normalized symbol also carries provenance metadata: `source` records how it was
produced (for example, tree-sitter or a heuristic layered on top of tree-sitter), and
`confidence` records how trustworthy that extraction is. Tree-sitter queries stay
declarative and language-specific; `core` receives only normalized symbols and relations.

## Model deviations

The implementation uses `SourceFile.project_root` and `Symbol.file_path` in place of the
brief's `workspace_id` and `file_id`. These are functional equivalents (a file is identified by
its workspace-relative path, scoped by the workspace root) and are not renamed.

## Structure and dependency rule

Synapse keeps a single boundary: core logic versus presentation.

```text
MCP presentation (FastMCP tools) ──► Core (model, parsing, indexing, querying)
```

- `core` holds the symbol model and the parse/index/query logic. It never imports `mcp`.
- `mcp` is a thin FastMCP layer that delegates to `core`.

Abstractions (interfaces, alternate backends) are introduced only when a concrete second
implementation appears — for example, the Phase 2 Go indexer.

## Component responsibilities

Each `core` sub-package exposes its public surface through a re-export `__init__.py`. Callers
import from the package (`core.index`, `core.languages`); the submodules named below are the
package's internal decomposition, not separate import targets.

- **`core.models`**: the normalized idiomatic symbol model, provenance metadata, and relation types.
- **`core.languages`**: the language seam. `registry` holds the supported language registry,
  extension detection, and tree-sitter naming; `grammars` loads only parser binaries already
  present in the local cache, so implicit downloads are rejected at the core boundary;
  `grammar_install` performs explicit installation; `queries` resolves `.scm` query files by
  language and query name.
- **`core.indexing`**: the ingest pipeline. `crawler` discovers indexable files and hashes them,
  `parser` parses with tree-sitter and maps captures to symbols, `pipeline` orchestrates
  crawl → hash → parse → upsert, and `references` reconciles reference edges. The parser lives
  here rather than under `core.languages` because it stays generic while the language seam churns.
- **`core.workspace`**: derives per-workspace storage paths and persists workspace metadata.
- **`core.index`**: the SQLite symbol index. `symbol_index` is the entry object; `schema`,
  `writes`, and `reads` hold schema/lifecycle, connection-explicit writes, and read projections.
  `repo_map` materializes a deterministic repository map — bounded directory areas
  (a trie partition with chain collapse, at most 12 areas), likely entrypoints
  (generic naming conventions only), trusted central declarations per area
  (exact/scoped incoming references only), and cross-area bridges (exact/scoped
  cross-file references plus declared-import segment matches) — stored as one JSON
  blob in `index_meta` under its own derivation-version key. It is recomputed
  inside the write transaction after every full and incremental index run; a
  version bump invalidates stored maps without forcing a workspace reindex, and
  readers derive a missing map live on their snapshot without writing.
- **`core.config`**: `settings` resolves ignore rules across three ordered layers — packaged
  defaults, global, project — and owns atomic writes. `ignores` is the shared gitignore-style
  matcher used by both the crawler and the watch layer, so crawl and watch filter identically.
  `ignore_presets` holds the packaged ecosystem templates, marker-file detection, and the
  first-run bootstrap.
- **`core.navigation`**: the deterministic two-call navigation contract behind
  `synapse_orient` and `synapse_inspect`. Synapse supplies compact structural
  evidence; the agent supplies semantic interpretation, query expansion,
  investigation planning, and synthesis — there is no natural-language keyword,
  intent, alignment, cluster, or adaptive-projection inference in core.
  Modules: literal term-to-declaration matching primitives with a simple crowd
  limit (`matching`), batched one-hop relation retrieval plus the stored-edge
  trust policy — containment and exact/scoped references are trusted, unique-name
  and unclassified stay heuristic, stored resolution and confidence are carried
  verbatim (`traversal`), ranked orientation (`orient`), one-snapshot batch
  inspection (`inspection`), shared payload plumbing — a deduplicated file table
  rebuilt on every assembly pass and compact symbol references (`render`), and the
  deterministic output budget with explicit truncation metadata (`budget`).
  Every read runs on one consistent SQLite snapshot via `SymbolIndex.read_session()`.
  Orientation evaluates up to 12 caller-supplied terms literally through
  exact-name, prefix/substring (at snake/camel word starts), literal-path,
  trusted-centrality, entrypoint, and repository-map signals; a term whose
  workspace-wide declaration-match count exceeds `max(25, symbols/100)` is
  reported as crowded and its non-exact candidates demote to weak evidence.
  Production ranks before tests and generated code. Name retrieval and path
  retrieval are separate channels — the name channel never matches file paths, so
  path-only rows cannot crowd out real name matches — and matched files are
  returned explicitly, including files with no indexed declarations, so every
  `files` row is referenced by a payload entry. Empty terms explicitly request
  repository-map orientation, which also projects a bounded set of trusted
  cross-area `bridges` addressed by area index; bridge examples are the first
  thing budget pressure removes. Inspection resolves 1–8 compact handles or
  stable IDs, retrieves one hop of incoming/outgoing relations in batched reads,
  groups references by far endpoint **and** call evidence, bounds source slices
  at 40 lines and relation groups at 12 per direction with visible totals and
  omitted counts, and surfaces unresolved references as named hypotheses rather
  than resolved matches.
  Call semantics are evidence-based, never inferred from a declaration kind: a
  site is a call only when its stored `usage_kind` is one the language advertises
  in `LanguageSpec.call_usage_kinds` (C#, TypeScript, TSX, and JavaScript:
  `invocation`, `object-creation`; Python: `invocation`). Everything else is
  neutral `refs_in`/`refs_out` carrying
  its usage kind verbatim, and an endpoint with both call and non-call sites
  splits into two groups rather than upgrading the non-call ones. Because call
  evidence is judged against the language the indexer recorded for the file the
  usage was written in, a language advertising no call kinds yields no callers or
  callees at all — which `coverage.extraction[].call_kinds: []` states outright.
  Compact handles (`s_` + 22 base64url chars of a 128-bit blake2b digest of the
  stable ID) are stored in the index under a unique constraint — a collision
  fails indexing explicitly — and backfilled by a schema migration; stable IDs
  stay canonical internally.
  Budgets are maxima, not padding targets: orientation defaults to 800 estimated
  tokens (clamped 400–1200), inspection to 2400 (clamped 500–4000, the public
  ceiling). Core callers choose a budget; the MCP tools deliberately do not
  expose one, so an agent cannot maximize it. The token budget is an estimate
  at 4 chars/token; the hard
  guarantee is a character cap of `token_budget * 4` on the exact serialized
  result, enforced through deterministic drop steps (weakest evidence first;
  caller/callee groups shrink to a compact navigation set before any selected
  source is removed; the first-requested symbol degrades last), then a minimal
  envelope, then a fixed truncation-only envelope. `payload_complete` reports payload truncation only;
  coverage counts report evidence bounds; task completeness is never claimed.
- **`mcp.server` / `mcp.tools` / `mcp.profiles`**: expose deterministic, token-frugal
  tools to agents through profile-tiered registration (`synapse serve --profile
  default|full`). The default profile is exactly the two-call navigation surface
  (`synapse_orient`, `synapse_inspect`) — both delegate the whole readiness
  decision to `core.lifecycle.ensure_navigation_ready`, which repairs a workspace
  that has no index, is not ready, is missing grammars, or carries a stale
  reference fingerprint. The full profile adds `synapse_ensure_workspace`,
  `synapse_index_workspace`, `synapse_search_symbols`, `synapse_get_definition`,
  `synapse_get_file_outline`, `synapse_get_dependencies`,
  `synapse_workspace_stats`, `synapse_project_map`, `synapse_get_file_dependencies`,
  `synapse_get_symbol_context`, `synapse_find_references`,
  `synapse_related_symbols`, `synapse_compact_context`,
  `synapse_watch_status`, `synapse_get_config`, `synapse_add_ignored_directories`,
  and `synapse_remove_ignored_directories` (19 tools total).
  The navigation tools return one compact JSON string so the token budget binds the
  exact wire payload; query tools return a uniform `{found: false, ...}` envelope
  instead of `None` so not-found never serializes as an empty result.
  Relations populated during indexing are `CONTAINS` (resolved member edges), `IMPORTS`
  (unresolved import target name), and `REFERENCES` (usage edges from reference queries).
  Reference resolution is syntactic and structural, never semantic.
  `core.indexing.resolution` binds a reference as `exact` only when the source syntax plus
  the indexed declarations prove one target — a fully-qualified name, an unambiguous dotted
  suffix, or a member reached through a receiver whose type is declared in the source.
  Receiver evidence includes explicitly typed locals and parameters, direct
  constructor-call assignments, `self`/`cls` inside the enclosing type, static
  type-name access, and factory calls whose declaration carries an explicit,
  same-file, resolvable return annotation (C# and Python both provide
  `LanguageSpec.reference_syntax`). Value-position proofs are conservative:
  a constructor call or static type-name access resolves only when no same-name
  non-type declaration contests the name and no local binding shadows the
  chain's root — assignments, untyped and lambda parameters, `def` statements,
  loop and comprehension targets, `with`/`except ... as` targets, walrus
  bindings, and import aliases (`import x as y`, `from m import x as y`) all
  count as shadows; annotation positions keep
  the type-kind gate because the syntax there already proves a type is meant.
  A rebinding in a conditional or loop block of the same variable frame voids an
  earlier binding's proof (Python scoping is per-function, not per-block), while
  a nested `def`/`class`/`lambda` is a separate frame and does not. `self`/`cls`
  is proof only structurally — the spelling must be the first parameter of a
  non-static enclosing callable and not reassigned; a typed or reassigned
  `self` follows its binding instead of the enclosing class. Static-decorator
  detection normalizes dotted paths and call forms to the base name, so
  `@builtins.staticmethod` and `@staticmethod()` count as static. Unsupported
  receiver evidence — inherited members, union or missing annotations,
  cross-file factory returns, dynamic receivers, shadows introduced by *plain*
  (non-aliased) imports of unindexed names — leaves the member honestly
  `ambiguous`.
  Narrowing by namespace, import, or enclosing type is weaker and recorded as `scoped`; a
  workspace-unique name match remains `unique-name` (a heuristic, never proof);
  multi-candidate names are `ambiguous` and unknown names `unresolved`. Each reference edge
  carries its source line, byte column, usage kind, and the dotted name written at the
  site. The resolver itself is language-agnostic: every syntax-specific fact it consults
  arrives via `LanguageSpec.reference_syntax`, so a language without that metadata falls
  back to unique-name resolution. `synapse_find_references` keeps confirmed and ambiguous
  matches in separate collections, pages both with the same window, and reports
  per-language extraction coverage from the language registry.
  Indexing stamps a reference-extraction fingerprint (packaged `.scm` queries +
  extractor version + schema version) into the index; `ensure_workspace` and plain
  `synapse index` detect a mismatch with a read-only probe and force an atomic full
  rebuild, so upgraded extraction semantics never silently reuse stale relations.
- **`core.index.contract`**: declares `SCHEMA_VERSION` (the shape of the file) and
  `INDEX_WRITER_CONTRACT_VERSION` (the invariants a *writer process* maintains on every
  incremental write). A watch daemon records the latter in `watch.json`; a live daemon
  whose provenance is absent, malformed, or different is stale, and `ensure_workspace`
  stops it before any repair touches the database. The contract is a declared integer
  rather than a fingerprint over the write path, so cosmetic edits do not force
  restarts, and rather than the package version, since two development builds share one
  version while implementing different contracts. Schema 6 moves the structural half of
  the contract into the file itself: `symbols.handle` is `NOT NULL` and shape-checked,
  so a writer from an older build is rejected by SQLite rather than trusted, even when
  it holds a connection opened before the migration. `core.index.integrity` owns the
  bounded read-only completeness probe and the deterministic, idempotent repair.
- **`core.provenance`**: reports which Synapse build is serving a call — version, the
  directory the package was imported from, schema/writer-contract/extractor versions,
  the reference fingerprint, and PEP 610 editability. Surfaced additively on `ensure_workspace`,
  `workspace_stats`, and `synapse status`, so a stale globally-installed tool is
  distinguishable from a live checkout. Installation identity only; no environment data.
- **`cli`**: provides global `install`, workspace `init`/`status`, project-scoped `setup`,
  `serve`, grammar, watch, doctor, and uninstall commands. Global install is the canonical
  onboarding path; project setup remains an advanced compatibility path.
- **`cli.ignore`**: `synapse ignore init|add|remove|list|migrate|presets`, scoped with
  `--scope project|global`; delegates every write to `core.config`.
- **`cli.config`**: `synapse config ignored-dirs list|add|remove`, deprecated aliases that
  delegate to `cli.ignore` so installed agent instructions keep working.
- **`adapters`** (`src/synapse/adapters/`, packaged data): the shared instruction snippet
  templates. One project template renders per agent from an `{agent_id}` placeholder.
- **`cli.adapters`**: the declarative agent seam. `model.py` defines the capability model,
  `registry.py` holds one data-only entry per agent, and `paths.py`, `instructions.py`,
  `skills.py`, `render.py` are agent-agnostic. Adding an agent is a registry entry plus tests —
  never a branch in generic code. Every capability (project/global MCP, project/global
  instructions, project/global skills) is independently optional, and home overrides are
  declared **per path**, because agents like Cline relocate some paths but not others.
- **`cli.config_codecs`**: format concerns in one place. JSON and YAML share a single merge
  algorithm — `dict`/`CommentedMap` are both mappings and `list`/`CommentedSeq` are both
  sequences, so shape-aware entry access is parameterised by container shape and key path
  rather than by agent. YAML is round-tripped through ruamel so user comments, key order, and
  anchor/alias pairs survive. TOML keeps a marker-block strategy because the standard library
  has no structure-preserving TOML writer.
- **`cli.installer`**: owns reversible MCP client config writes, delegating format handling to
  `cli.config_codecs`, so uninstall removes only Synapse-owned content.
- **`core.watch.daemon`**: owns detached process start, health verification, and bounded
  lifecycle waits.
- **`core.lifecycle`**: owns workspace state, lazy grammar/index initialization, query
  readiness, and daemon repair. CLI and MCP call the same lifecycle.
  Navigation readiness is the complete decision, not a daemon health check:
  `navigation_repair_reason` returns `no-index`, `not-ready`, `stale-writer`,
  `missing-grammars`, `stale-references`, or one of the handle-completeness reasons
  (`incomplete-handles`, `handles-unenforced`, `unreadable-index`), checked
  cheapest-first and strictly read-only — it never constructs a `SymbolIndex`, because
  that migrates the schema and takes a write transaction against a database a daemon or
  a concurrent rebuild may own.
  `ensure_navigation_ready` repairs through `ensure_workspace` only when a reason
  exists, then re-probes before letting the call proceed. Freshness belongs in
  readiness because schema migration alone can carry relations built under older
  extraction semantics into a workspace that reports READY. Handle completeness belongs
  there for the same reason at a different layer: orientation renders a handle from the
  stable id while inspection resolves the persisted column, so a row without a usable
  handle produces a public handle that can never resolve, and neither the schema version
  nor the fingerprint can see it. A lost repair race
  (`WatchAlreadyRunning`, a locked database, a daemon that would not stop) is waited
  out to a bounded deadline rather than raised, since an agent cannot act on it.

## Configuration layering

Ignore rules are **ordered, and the last matching rule wins**. Rules concatenate in layer order:
packaged defaults, then the global layer, then the project layer. A later layer can therefore
re-include what an earlier one ignored — `!node_modules/` in a workspace genuinely turns off the
built-in. `.git` is the single exception: it is pinned as a directory at any depth and no rule can
negate it, because un-ignoring it costs unboundedly and buys nothing.

Each writable layer reads from an ignore file, falling back to the legacy JSON list:

| Layer | Ignore file | Legacy fallback |
| --- | --- | --- |
| built-in | — | `core/default_ignored_directories.json` (packaged) |
| global | `~/.config/synapse/ignore` | `ignored_directories` in `~/.config/synapse/config.json` |
| project | `<workspace>/.synapseignore` | `ignored_directories` in `<workspace>/.synapse/config.json` |

When an ignore file exists it **supersedes** that layer's `ignored_directories`, which is reported
as `shadowed_project_json` and warned about rather than silently dropped. `watch.*` in the JSON is
unaffected. Any write adopts the ignore file, migrating the legacy entries into it in the same
write, so a layer never has two live sources. `synapse ignore migrate` does that explicitly.

Ignore files use gitignore syntax, compiled by `pathspec` (`GitIgnoreSpecPattern`): a bare name
matches at any depth, a trailing slash matches directories only (`build/`), a leading slash anchors
to the workspace root (`/dist`), embedded slashes anchor implicitly (`src/generated/`), globs match
file names (`*.min.js`, `test_?.py`, `[Bb]uild/`, `docs/**`), and a leading `!` re-includes. `#`
starts a comment. A line that cannot be compiled is skipped with a warning and reported in
`ignore_problems`; failing the whole file over one typo could silently un-ignore a build tree.

Paths are decided **component by component from the root down**, exactly as git decides them: the
first component that resolves to ignored wins and nothing beneath it can be re-included. That is
what makes `os.walk` pruning equivalent to evaluating full paths, and it is why `!build/keep.py` is
inert when `build/` is ignored — the same behavior git itself has.

Legacy `ignored_directories` entries map to directory-only rules (`node_modules` → `node_modules/`,
`/build` → `/build/`, `src/generated` → `/src/generated/`), so migrating never causes a file to
become newly ignored.

Because `core.config` is re-read on every crawl, an ignore change converges on the next sweep
without a reindex or daemon restart.

`.synapseignore` is meant to be committed, so the whole team indexes the same tree. On first-run
initialization, `ensure_workspace` creates one from the ecosystems it detects (marker files at the
root or one level down) before the first crawl. It never touches an existing file, writes nothing
when no ecosystem is detected, degrades to a warning on any filesystem error, and reports what it
wrote in `EnsureWorkspaceResult.ignore_bootstrap`. Opt out with `SYNAPSE_NO_IGNORE_BOOTSTRAP=1` or
`"auto_ignore_bootstrap": false`. The file is flat — no managed marker block — because it is
version-controlled, and git already provides the history and conflict handling a managed region
would duplicate. Synapse only ever appends to it or deletes an exact line.

## Agent adoption layers

Synapse uses five global layers to make agents reach for structural context first:

1. A marker-managed global instruction describes the two-call navigation flow
   (translate the task into repository vocabulary → `synapse_orient` →
   `synapse_inspect`).
2. MCP server instructions advertise the same Synapse-first flow; no bootstrap
   call is needed because the navigation tools initialize lazily.
3. Entry-tool docstrings describe when to prefer structural tools over text search.
4. The managed `synapse-code-context` skill supplies the detailed multi-tool workflow.
5. For agents whose hooks can add context while allowing the call (Claude Code, Crush,
   Qwen Code), a suggest-only pre-shell hook (`synapse hook <codec>-pre-bash`) injects a
   Synapse reminder when shell exploration commands run inside an indexed workspace. It
   never blocks or auto-approves the command. The decision logic is shared in
   `cli/hooks/core.py`; only the wire shape differs per agent (`cli/hooks/codecs.py`).

The instruction is the mandatory trigger and does not depend on skill activation. The skill
is supplementary because implicit skill matching alone is not a reliable initialization
contract. The hook exists because advisory instructions do not reliably shape the first
tool call of a session. Project setup can still install repository instructions for shared
integrations.

## Runtime lifecycle

The supported user path is globally configured and daemon-backed per workspace:

1. `synapse install <agent>` writes a portable user MCP entry, global instruction, and skill.
2. A new MCP process resolves the nearest Git root and starts in bootstrap mode when no
   metadata exists.
3. The first navigation call (or an explicit `synapse_ensure_workspace` on the full
   profile) installs parsers, builds the initial index, and starts the detached
   polling daemon, which becomes the incremental index writer for that workspace.
4. Initialized MCP sessions restore a missing daemon; navigation tools repair
   uninitialized or degraded state lazily, while full-profile query tools reject it
   instead of exposing an empty or stale index.

Bare `synapse` displays global install help. Project setup, manual indexing, independent MCP
config installation, and foreground watching remain explicit advanced entry points.

## Indexing flow

The planned Phase 1 flow is crawl → hash → parse → map to symbols → upsert. File hashes
enable re-indexing changed files only. Merkle-style workspace summaries may later avoid
walking unchanged subtrees.

## Query flow

An MCP tool receives typed input, calls the matching `core` function, and returns compact
structural data. Tools return data structures instead of prose.

## Incremental indexing strategy

Phase 1 stores file-level hashes in SQLite and reparses changed files. Later work can add
Merkle tree summaries for directories and richer dependency invalidation.

## Roadmap

- **Phase 1**: Python MVP, tree-sitter parsing, SQLite index, MCP tools, CLI, and adapters.
- **Phase 2**: Go `synapse-indexer` subprocess for faster parsing and indexing.

Planned but **not yet implemented** layers:

- Native OS file-event watching behind the `WatchBackend` protocol
  (`core/watch/backend.py`); the daemon is polling-only today.

## Open questions and risks

- Wheels for `tree-sitter` and `tree-sitter-language-pack` on the newest Python may lag;
  the supported floor is Python 3.12.
- MCP v2 migration should wait until v2 is stable; dependencies pin `<2` for now.
- Grammar/core version coupling is mitigated by `tree-sitter-language-pack`; individual
  grammar packages remain a fallback if the pack lacks a required language.
