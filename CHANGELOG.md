# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Initial head slices are now verified against the indexer's canonical content hash
  with the same single-read, fail-closed check continuation windows already use: a
  file edited after indexing reports an explicit `src_stale` entry and a
  `coverage.source_stale` handle list instead of serving post-index bytes as the
  stored span, and head and continuation windows can never mix two file versions;
  previously the head read returned whatever bytes were on disk. The full-profile
  `synapse_get_symbol_context` body read gets the same verification and now reports
  `body_stale`.
- Workspace metadata is now written atomically (temporary file plus rename), and a
  corrupt or truncated `metadata.json` reads as "not initialized" so the ensure path
  regenerates it; previously a crash mid-write could leave invalid JSON that made
  every status, ensure, and watch probe raise instead of self-healing.

## [0.5.3] - 2026-08-16

Instruction and read-side projection release; no schema, write-path, tool-surface,
or payload-budget change. Run `synapse install <agent>` again to refresh the managed
skill.

### Added
- `synapse_inspect` entries now carry a `siblings` roster: the other top-level
  declarations of the inspected symbol's file, each with a handle, name, and
  definition line, capped at 10 with an exact `siblings_omitted` count. Module-level
  neighbours are now reachable without knowing their names — previously a pure-MCP
  agent had no route to a same-file sibling (no relation edge exists between
  siblings, and module-level symbols have no parent container).

### Changed
- Risk investigation discipline (0.5.3 candidate): each requested risk is its own
  ledger facet with its own closing attempt — inspect the guard, probe, or recovery
  path before selecting a risk, preferring a verified chain over a more striking
  unverified one. A facet carrying a requested deliverable may take one further
  facet-scoped closing attempt before being reported unresolved.

### Fixed
- Callee and outgoing-reference groups whose every call site lies beyond the fixed
  40-line source slice are now retained in preference to groups already visible in
  the returned source when the response budget forces drops; previously exactly the
  relations the agent could not see were dropped first.
- Resolved relation endpoints now carry their definition line (`l`), making every
  endpoint a complete file:line citation.

## [0.5.2] - 2026-08-16

Instruction-only recalibration of the 0.5.1 workflow after its focused matched
comparison failed on factual accuracy and token economy. No engine, tool-surface,
schema, or payload change.

### Changed
- Reframed the reliability-claim discipline: a reported risk is either a verified
  failure path or an explicitly unresolved hypothesis, and both are valid
  deliverables — the skill now forbids completing an evidence chain by assumption
  instead of demanding a complete chain for every asserted risk. The server
  handshake carries the same calibration in one sentence.
- Bounded the facet ledger: facets are still derived from each material clause
  (deliverables included) but merged so the ledger stays small (typically 3–7),
  removing the facet-count-proportional retrieval pressure observed in the
  matched run.
- Replaced the standalone pre-answer checklist with a compact ledger-driven
  synthesize-and-stop rule (write the answer from the ledger, not the most recent
  payloads; no additional tool calls), after the checklist correlated with extra
  broad searches and overlapping rereads.

## [0.5.1] - 2026-08-14

Instruction-only patch release: the navigation engine, MCP tool surface, wire
schemas, and payload budgets are unchanged. Run `synapse install <agent>` again to
refresh managed instruction files and the managed skill.

### Changed
- Rewrote the managed `synapse-code-context` skill around an operational facet
  ledger: one facet per material clause of the request (including requested
  deliverables such as risks or recommendations), three tracking states
  (`verified`/`partial`/`missing`) with best evidence recorded at first sight, a
  reliability-claim chain (initiating fault → permitting path → surviving guard →
  observable outcome) that downgrades unverified risks to unresolved hypotheses,
  and a no-tool pre-answer check that accounts for every facet before synthesis.
  Bounded gap closure now prefers an offered continuation token over shell
  re-reads, and a mostly-unmatched orientation is refined with path fragments
  rather than more invented names.
- Aligned the MCP server handshake and both adapter instruction snippets with the
  same contract in single-sentence form (facet deliverables, full-coverage
  answers, guard-verified risks); the detailed workflow lives only in the skill.
- Managed-content ownership no longer uses HTML-comment markers. Installed skills
  keep ownership outside agent-visible content in a hidden `.synapse-managed.json`
  sidecar containing the exact SHA-256 of each managed file. Updates and removals
  fail closed when those files drift, and unknown files are preserved. Instruction
  blocks in shared `AGENTS.md`/`CLAUDE.md`-style files are anchored on their own
  headings (a managed block extends to the next heading, so content appended directly
  after it should start with a heading). Legacy marker-delimited installs are still
  recognized and migrated on the next install or removal.
- Added instruction-size ceilings to the test suite so no surface silently grows
  into a duplicated checklist.

## [0.5.0] - 2026-08-13

> **Breaking beta release.** Synapse remains pre-1.0, so minor releases may contain
> incompatible changes. This release replaces the default MCP surface and several
> configuration and response contracts. After upgrading, run `synapse install <agent>`
> again to refresh managed integration files; existing workspace indexes are repaired
> automatically on the next navigation call.

### Security
- Raised the direct `cryptography` floor to `>=50.0.0`, resolving
  `PYSEC-2026-3552` in the locked release environment.

### Removed
- **Breaking:** `synapse_query_context` and the `core/context` task-answer engine are
  removed without deprecation; the feature was unreleased. Natural-language keyword
  extraction, intent classification, alignment scoring, cluster establishment, adaptive
  projection, seed tiering, and question-aware flow ranking are gone with it. Synapse
  supplies structural evidence; the agent supplies interpretation and synthesis.

### Added
- `.synapseignore`: a portable, committed, plain-text ignore file at the workspace root using
  **gitignore syntax** — globs (`*.min.js`, `docs/**`), directory-only rules (`build/`), root
  anchoring (`/dist`), `#` comments, and `!` negation. Patterns are compiled with `pathspec`
  but the ordered rule list is Synapse's own, so every rule reports its layer, file, and line.
  The global layer gains the same treatment at `~/.config/synapse/ignore`.
- Ignore rules are now **ordered, last-match-wins** instead of a union, so a later layer can
  re-include what an earlier one ignored (`!node_modules/` turns off a built-in). `.git` is
  pinned and cannot be re-included. Paths are decided component by component from the root
  down, exactly as git decides them, which keeps `os.walk` pruning correct under negation and
  makes a negation beneath an ignored directory inert — the same behavior git has.
- File-level ignoring. The crawler previously pruned directories only; `*.min.js` and
  `*.generated.cs` now work, and the watch layer checks the full relative path rather than
  just the directories leading to a file.
- `synapse ignore init|add|remove|list|migrate|presets` with 14 ecosystem templates (node,
  dotnet, java, python, go, rust, swift, android, php, ruby, unity, ml, terraform, editors).
  `init` detects ecosystems from marker files at the root or one level down.
- First-run bootstrap: `ensure_workspace` creates `.synapseignore` from the detected
  ecosystems before the first crawl, reported in `EnsureWorkspaceResult.ignore_bootstrap`. It
  never touches an existing file, writes nothing when nothing is detected, and degrades to a
  warning on any filesystem error. Opt out with `SYNAPSE_NO_IGNORE_BOOTSTRAP=1` or
  `"auto_ignore_bootstrap": false`.
- `pathspec` is now a runtime dependency. It is used only to compile patterns to regexes — no
  filesystem access — so the local-first guarantee is unchanged.
- `synapse_orient`: ranked, production-first orientation over up to 12 agent-chosen
  literal repository terms (exact-name, prefix/substring at snake and camel word starts,
  literal path, trusted centrality, entrypoint, and repository-map signals, with a simple
  crowd penalty). Name retrieval and path retrieval are separate channels, so a file whose
  path contains the term can no longer fill the bounded page and hide real name matches.
  Matched files are returned explicitly — including files with no indexed declarations —
  and every `files` entry is referenced by a payload entry. Empty terms return a
  repository-map orientation with bounded, trusted cross-area `bridges`. Default budget
  800 estimated tokens, clamped 400-1200.
- `synapse_inspect`: one-snapshot batch inspection of 1-8 compact handles or stable IDs —
  definitions, bounded source slices (<=40 lines), parents/children, and grouped
  relations carrying stored resolution, confidence, and usage kind verbatim, plus
  unresolved hypotheses. Default budget 2,400 estimated tokens, public maximum 4,000.
- Stateless source continuation for incomplete `synapse_inspect` slices. `src.next`
  retrieves the next non-overlapping window, while `remaining_lines` reports the exact
  unreturned span so an agent can decide whether another call is worth its context cost.
  Continuation-only calls may return up to 256 lines and shrink deterministically under
  wire pressure; tokens bind the symbol, exact next line, stored span, and indexed content
  hash, and an edited file is rejected as stale instead of being spliced across versions.
- Deterministic compact symbol handles (`s_` + 22 base64url characters from a 128-bit
  blake2b digest of the stable ID), stored as a non-null, shape-checked value under a
  unique index (schema v6) and backfilled by an in-place migration; a digest collision
  fails indexing explicitly. Full-profile symbol summaries include the handle alongside
  the canonical `symbol_id`.
- Per-language `call_usage_kinds` on `LanguageSpec`, with `call_usage_kinds()` and
  `is_call_usage()` accessors: the single place where usage-kind vocabulary becomes call
  semantics. C#, TypeScript, TSX, and JavaScript prove a call with `invocation` and
  `object-creation`; Python with `invocation`.
- Python reference queries now label their four captures (`invocation` for direct and
  attribute calls, `base-type` for superclasses, `decorator` for bare decorator names).
  The captured spans are unchanged — only the previously absent `usage_kind` is new.
- TypeScript, TSX, and JavaScript reference queries label their three call captures
  (`invocation` for direct and member calls, `object-creation` for `new` expressions;
  spans unchanged), and the three languages advertise honest `reference_limitations`:
  `dynamic-dispatch`, `member-call-receiver-types`, `import-alias-relations`,
  `local-variables`, `configuration-strings`, `non-call-references`.
- Named TypeScript, TSX, and JavaScript generator declarations (`function*`,
  `async function*`, exported or not) are indexed as normalized functions.
- C# reference queries capture invocations through a receiver
  (`repository.Save(1)`), which were previously indistinguishable from a member read.
- Name-only symbol retrieval (`search_symbol_names_page`), indexed-language lookup by
  path (`languages_by_path`), and bounded unresolved-reference reads with exact totals
  (`unresolved_references_by_name`) in the read projections.
- Shared bounded source-slice helper (`core/index/source.py`) and a literal-path file
  lookup (`files_matching_path`).
- Nine agent adapters for `synapse install` / `synapse uninstall` / `synapse setup`:
  `hermes`, `gemini`, `copilot`, `cursor`, `windsurf`, `cline`, `kiro`, `qwen`, and
  `continue`. Capabilities are declared per agent and independently optional, so an
  unsupported scope fails with a capability-specific message and unsupported instructions
  or skills are skipped cleanly instead of guessed. See the support matrix in
  `docs/installation.md`, including agents deliberately deferred with their blockers.
- YAML MCP configuration support for both container shapes in use: Hermes' mapping under
  `mcp_servers` and Continue's list under `mcpServers` matched on an inner `name`.
  Configs round-trip with `ruamel.yaml`, so comments, key order, and anchor/alias pairs
  survive install and uninstall.
- Project-scoped skill installation for agents that document a project skill directory.
  `synapse setup` installs it alongside the project instruction file; `--no-skill` skips
  it and `synapse uninstall --keep-skill` retains it.
- Suggest-only Claude Code `PreToolUse` hook (`synapse hook claude-pre-bash`): when a
  shell exploration command runs in an indexed workspace it injects a Synapse reminder via
  `additionalContext`, without blocking or auto-approving the command. Installed by
  `synapse install claude-code` (skip with `--no-hook`).
- Runtime provenance (`runtime`) on `synapse_ensure_workspace`, `synapse_workspace_stats`,
  and `synapse status`: package version, import directory, schema and extractor versions,
  the reference fingerprint, and whether the install is editable (PEP 610). No environment
  or secret data is exposed.
- Build-output directories in the default ignore list: `obj`, `bin`, `target`, `out`,
  `coverage`, `.vs`, `.gradle`, `.next`, `.nuxt`, `.tox`, `.pytest_cache`, `DerivedData`,
  `Pods`.

### Changed
- **Breaking:** an ignore file supersedes that layer's `ignored_directories` in `config.json`.
  The superseded entries are warned about and reported as `shadowed_project_json`, never
  silently dropped, and the first write migrates them across. `synapse ignore migrate` does it
  explicitly. `watch.*` settings are unaffected.
- **Breaking:** `synapse_get_config`'s `options.ignored_directories` — including its
  `effective` list of `{value, sources}` — is replaced by `options.ignore_rules`, an ordered
  list bounded by `rules_total`/`rules_complete`, plus `skipped_lines` and
  `shadowed_project_json`. Under negation there is no flat set of ignored paths, and emitting
  one would be a false completeness claim. The `IgnoredDirectoryEntry` dataclass is removed.
- **Breaking:** removing a built-in or globally-inherited ignore no longer raises. Both
  `synapse_remove_ignored_directories` and the CLI now append a negation and report it in
  `negated`; a command that used to exit 2 exits 0.
- **Breaking:** file-level rules can remove already-indexed files on the next sweep. That is
  correct — those files match an ignore rule — but it will look like data loss if unexpected.
- `synapse config ignored-dirs list|add|remove` is deprecated. It still works and prints a
  notice pointing at `synapse ignore`; the tool names `synapse_add_ignored_directories` and
  `synapse_remove_ignored_directories` are deliberately unchanged so installed agent
  instructions keep working.
- **Breaking:** `callers` and `callees` now contain call-proven sites only. A call is
  never inferred from either endpoint's declaration kind, so a C# `declared-type`
  reference no longer makes a method a caller of the type it declares. Everything else is
  returned as neutral `refs_in`/`refs_out` (replacing the former `refs`) with its usage
  kind verbatim. An endpoint that is both called and named in a type position yields two
  groups rather than one upgraded group. Languages advertising no call usage kinds report
  no callers or callees at all; `coverage.extraction[].call_kinds: []` is the explicit
  signal.
- **Breaking:** the default MCP profile is exactly the two navigation tools (measured
  schema cost approximately 700 estimated tokens, gated at 800; previously five tools at
  roughly 1,507).
  The full profile contains 19 tools and retains `synapse_ensure_workspace` for explicit
  setup, diagnostics, and recovery.
- Workspace readiness for the navigation tools is now a complete decision in
  `core.lifecycle` (`navigation_repair_reason`, `ensure_navigation_ready`), not a daemon
  health check in the MCP layer. A navigation call repairs a workspace that has no index,
  is uninitialized or degraded, is missing grammars, or carries a stale reference
  fingerprint — and re-verifies before answering. Previously a `READY` daemon was treated
  as sufficient, so a workspace that migrated its SQLite schema while keeping relations
  built under older extraction semantics could serve stale evidence indefinitely. A
  healthy, current workspace performs no ensure and no re-index; the probe is read-only
  and never allocates the cache directory. Losing a repair race to another process is
  absorbed and waited out rather than surfaced as a lock error.
- The persisted index now declares an independent writer-contract version. Navigation
  stops a live watch daemon whose write invariants are missing or incompatible before a
  repair touches SQLite, preventing an older process from reintroducing handle-less rows
  after schema migration.
- Inspection coverage reports what the evidence is actually made of: per-language
  `completeness` (so an empty limitation list can no longer read as complete),
  `call_kinds`, `limitations`, an explicit `exhaustive: false`, the languages that
  produced the returned relations (not only the selected symbols'), exact
  `hypotheses_total`/`hypotheses_omitted`, and separate `source_truncated`,
  `source_shortened`, `source_omitted`, and `source_unavailable` lists. Orientation
  coverage reports its fixed caps plus `name_omitted` and `files_omitted`.
  `payload_complete` remains strictly about serialization, never about evidence or task
  completeness.
- `path_scope` now narrows both orientation retrieval channels inside the query, before
  their bounds, and the crowd metric counts the scoped space. Previously a global page was
  fetched and filtered afterwards, so enough out-of-scope declarations sharing a term's
  stem consumed the page, hid every in-scope match, and made the term report as unmatched.
- Orientation's name channel now excludes import statements in SQL, before the count and
  the page bound. Imports previously filled the bounded page, inflated the crowd count,
  and hid a real declaration — a file with 30 `import handler_*` lines made an existing
  `handler_target` report as unmatched and crowded. `synapse_search_symbols` is unchanged
  and still returns imports, including for an explicit `kind="import"`.
- Crowding is measured against the searchable declarations of the scope, not the whole
  workspace symbol count, so both sides of the `max(25, population / 100)` comparison
  describe the same search space. A term saturating a small scope inside a large
  workspace is now correctly classified as crowded.
- A path term's shape decides how it matches, in one shared definition used by retrieval,
  the page total, the distinct-union count, and orientation's acceptance: path-shaped
  terms (containing `/` or `.`) may match a substring, bare words match only an exact
  path or a whole trailing path component. Previously the SQL matched the broad set while
  orientation accepted the narrow one, so a response could report a term unmatched and in
  the same breath claim 25 matching files were omitted.
- **The full-text branch now executes.** `symbols` and `symbols_fts` both declare `name`,
  `qualified_name`, and `file_path`, and the ranking used them unqualified inside `CASE
  WHEN` expressions, so SQLite raised `ambiguous column name` — swallowed into an empty
  result, which silently routed every search through the substring fallback while all
  result-based tests kept passing. Every joined column is now qualified, and the
  `OperationalError` catch is narrowed to genuine FTS query errors (SQLite prefixes those
  with `fts5:`) so a statement defect fails loudly instead of disabling the index.
  **Consequence:** with FTS live, a token-prefix match can now outrank a shorter
  substring-only match — for `handler`, `[aaa_handler_bbb, xhandler]` rather than
  `[xhandler, aaa_handler_bbb]`. This is the intended "FTS prefix first, substring
  fallback" ordering; the page total keeps its exact substring semantics.
- FTS candidates must satisfy the same literal predicate as the fallback and the count.
  FTS5 tokenizes `.`, `-`, and `_` alike, so a search for `foo.bar` matched `foo-bar-00`
  and thirty such look-alikes filled the page, leaving the one real match unreachable and
  the page self-contradictory (25 rows returned against a total of 1). The literal
  predicate is now applied inside the FTS statement — not as a Python post-filter, which
  would let false candidates keep consuming the SQL `LIMIT` — so FTS rows are always a
  subset of the literal result set and the fallback retains room to find mid-token
  matches.
- The path channel returns exact page metadata, so `files_omitted` counts every distinct
  matching file rather than only the ones the retrieval limit happened to return, and
  `path_capped` marks matches that were never retrieved at all — distinct from the budget
  truncation `budget.dropped` already accounts for.
- **Breaking:** `coverage.workspace_languages` is removed. When a selected symbol has zero
  incoming references, `coverage.extraction` itself now calibrates the other indexed
  workspace languages, marked `"evidence": false`, so a zero-caller answer can be read
  against their `call_kinds` and `limitations` instead of naming languages the agent has
  no metadata for. Evidence-producing languages omit the key, so normal payloads are
  unchanged.
- `source_truncated` and `source_shortened` are no longer conflated: truncation means the
  definition outgrew the fixed 40-line slice, shortening means the wire budget removed
  lines the slice would otherwise have held. A body under the fixed cap that the budget
  shortened is no longer reported as both, and a body shorter than the reduced bound is
  not reported as shortened at all. Entry-level `src.truncated` keeps its documented
  meaning — the displayed text is incomplete, whatever the cause.
- Python shadow coverage widened: lambda parameters, import aliases, loop and
  comprehension targets, `with`/`except ... as` targets, and walrus bindings shadow type
  names, so a shadowed name is never proven a constructor or static type access. Static
  decorator detection normalizes dotted and call forms.
- Python receiver resolution is conservative about value-position type proofs: a
  constructor call or static type-name access resolves `exact` only when no same-name
  non-type declaration contests the name and no local binding shadows the chain's root;
  a rebinding in a conditional or loop block of the same variable frame voids an earlier
  binding's proof; `self`/`cls` counts only structurally. Annotation-position proofs are
  unchanged. New documented limitation: `unindexed-import-shadows`.
- The managed skill, adapter instruction snippets, MCP server instructions, Claude hook
  reminder, and documentation are rewritten around the bounded navigation lifecycle:
  translate the request into repository vocabulary -> `synapse_orient` ->
  `synapse_inspect` with 2-3 initial facet-diverse anchors -> follow 1-2 returned
  relation handles for still-open facets -> one bounded gap-closing attempt per
  partial/missing facet (a relation handle, a refined orientation, or one facet-scoped
  shell fallback) -> report each facet verified or unresolved. Two calls remain the
  common fast path, not a cap.
- `SCHEMA_VERSION` is 6, `INDEX_WRITER_CONTRACT_VERSION` is 1, and
  `REFERENCE_EXTRACTOR_VERSION` is 7. Existing workspaces perform the required in-place
  migration and one fingerprint-forced rebuild on the next navigation call,
  `ensure_workspace`, or `synapse index`.
- Query tools that previously returned `null`/empty content for a missing symbol or file
  (`synapse_get_definition`, `synapse_get_file_outline`, `synapse_get_file_dependencies`,
  `synapse_get_symbol_context`, `synapse_related_symbols`, `synapse_compact_context`) now
  return a uniform `{found: false, target, reason, hint}` envelope, and no longer wrap
  structured results in `{"result": ...}`.
- Agent adapters are declarative: `cli/adapters.py` became the `cli.adapters` package and
  format handling moved into `cli.config_codecs`, where JSON and YAML share one merge
  algorithm. Twelve agent-id conditionals were removed from generic install, uninstall,
  render, and path-resolution code. Claude Code, Codex, and OpenCode behaviour, including
  their serialized MCP config, is unchanged.
- Home-directory overrides resolve per path rather than per agent, as Cline requires:
  `CLINE_DATA_DIR` relocates the global rules and skill but not `~/.cline/mcp.json`.
  `CODEX_HOME` and `XDG_CONFIG_HOME` keep their existing behaviour.

### Fixed
- Watch-lock acquisition no longer deletes a lock file in the brief interval between
  its atomic creation and the owner PID write. Concurrent stale-index repairs therefore
  cannot both enter the rebuild critical section and collide inside SQLite; old empty or
  malformed lock files are still recovered after a bounded initialization grace period.
- Decorated Python definitions (`@decorator def ...`, decorated methods, decorated async
  defs) are indexed as their actual function declarations; previously the anchored
  tree-sitter patterns missed `decorated_definition` wrappers, which made every
  FastMCP-decorated tool invisible to search and mis-anchored the references in their
  bodies.
- Python structural reference resolution (`PYTHON_REFERENCE_SYNTAX`): member calls resolve
  `exact` when receiver evidence proves the containing type — typed parameters and locals,
  direct constructor assignments, `self`/`cls` inside the enclosing class, static
  type-name access, and factory calls with an explicit same-file return annotation;
  everything weaker stays honestly `ambiguous`. Unsupported and documented: inherited
  members, union or missing annotations, cross-file factory returns, dynamic receivers,
  import-scope narrowing.
- `synapse_install opencode` no longer adds a `$schema` key to an existing
  `opencode.json` that lacked one.
- `synapse_project_map` excludes namespaces and imports from `top_symbols` entirely and
  aggregates deduplicated namespace names under `namespaces` (`items`, `total`,
  `truncated`), so per-file C# namespaces no longer crowd out or pad the declaration list.
- `synapse_find_references` pages `possible_items` with the same `limit`/`offset` as
  confirmed `items` and reports a matching `possible_page` block, so ambiguous results
  beyond the first page are reachable instead of being re-served from the start. Both
  collections are ordered by file path, line, byte column, then relation id.
- C# file-scoped namespaces (`namespace X;`) scope the rest of the file, so types declared
  under them carry fully-qualified names instead of bare ones.
- `ensure_workspace` stops a live watch daemon before a forced rebuild instead of crashing
  on the watch lock, and a schema upgrade migrates the `relations` table in place.
- MCP and navigation tests no longer touch the user-global Synapse data directory:
  autouse fixtures isolate `SYNAPSE_DATA_DIR` per test.

## [0.4.0] - 2026-07-28

### Security
- Raised the `mcp` floor to `>=1.28.1`, which validates `Host`/`Origin` on the WebSocket
  server transport. Synapse serves over stdio only, so the issue was not reachable here.
- Added a direct `pydantic-settings>=2.14.2` floor. It is transitive via `mcp`, which floors
  it at `>=2.5.2`, so the constraint is stated here to keep the `NestedSecretsSettingsSource`
  symlink-escape fix for downstream installs.

### Added
- Dependabot configuration for the `uv` and `github-actions` ecosystems, and a
  `pip-audit` dependency-audit job in CI.
- Contributor documentation: `CONTRIBUTING.md` with the local quality gate, the
  language-addition path, and the MCP tool contract; `SECURITY.md` with the private
  reporting channel and the local-first threat model; GitHub issue forms and a pull
  request template.
- Global `synapse install <agent>` onboarding with portable user-scoped MCP configuration,
  managed global instructions, and the `synapse-code-context` skill.
- Lazy `synapse_ensure_workspace` initialization plus CLI `init` and read-only `status`
  commands.
- Unified `synapse setup <agent>` onboarding now installs missing grammars, builds the
  workspace index, writes project-scoped MCP configuration and managed instructions, starts
  the watch daemon, and validates the completed integration.
- Installation and MCP tool references under `docs/`.
- Workspace-local configuration at `<workspace>/.synapse/config.json`, unioned with the
  packaged defaults and the global user config. Meant to be committed.
- MCP configuration tools `synapse_get_config`, `synapse_add_ignored_directories`, and
  `synapse_remove_ignored_directories`, so agents configure Synapse through a typed contract
  instead of hand-editing JSON. `synapse_get_config` is self-describing: it returns accepted
  input forms, per-entry provenance, the file writes land in, and when a change takes effect.
- A shared ignore matcher (`core.config.ignores`) used by both the crawler and the watch layer,
  so crawl and watch filter identically.
- `synapse config ignored-dirs` gained `--scope project|global` and `--path`.

### Changed
- Bumped `actions/upload-artifact` and `actions/download-artifact` to `v5` so release
  workflows stop running on the deprecated Node 20 shim.
- Reorganized `core` into cohesive sub-packages — `core.config`, `core.index`, `core.indexing`,
  and `core.languages` — each exposing its public surface through a re-export `__init__.py`.
  The `core.index_*` name prefixes are gone. Internal module paths only; the CLI, the MCP tool
  contracts, and the packaged data layout are unchanged.
- `ignored_directories` entries now accept gitignore-style anchoring: a bare name matches at
  any depth, while `/build` or `src/generated` anchors to the workspace root. Previously any
  separator was rejected.
- **Breaking:** `synapse config ignored-dirs add|remove` now writes the project config by
  default; pass `--scope global` for the previous behavior. The resolved target path is always
  printed.
- **Breaking:** removing a built-in ignored directory now fails with exit code 2 instead of
  silently doing nothing, matching the MCP contract.
- `.synapse` is now a built-in ignored directory.
- Global install is now the canonical user flow; project-scoped setup remains available for
  compatibility and shared repository configuration.
- MCP can expose bootstrap tools for a new workspace, while all query tools require an
  initialized index and healthy daemon.
- MCP startup now restores and verifies the workspace watch daemon before exposing tools;
  daemon health is a hard doctor requirement.
- Codex MCP configuration defaults to project scope, and bare `synapse` displays install help
  instead of starting an unconfigured stdio server.
- Agent instruction snippets now lead with `synapse_ensure_workspace`, cover the full
  navigation tool set, and demote `synapse_index_workspace` to recovery-only, matching the
  skill, server instructions, and tool reference.
- Managed skill files are installed per agent: only Codex receives `agents/openai.yaml`;
  reinstall and removal clean up the legacy copy for other agents.
- MCP tool docstrings document parameter rules (`symbol_id` OR `name`), return shapes,
  valid `kind` values, and cross-tool disambiguation; server instructions now advertise the
  architecture and relation tools.

### Fixed
- Config writes are now atomic (temp file plus rename), so the watch daemon can never read a
  partially written config, and they no longer append `os.linesep` in text mode (which
  produced `\r\r\n` on Windows).
- `synapse doctor` now checks for `synapse_ensure_workspace`, which was missing from its
  expected-tool list despite being the mandatory entry point.
- The watch layer applied directory ignore rules to filenames as well as directories; it now
  matches the crawler exactly.

### Removed
- Unused packaged data: static `mcp-config-template.*` files (configuration is rendered
  programmatically) and the empty Claude Code hooks placeholder.
- `core.config.validate_directory_name` and `core.watch.events.default_normalizer`, both
  superseded by `core.ignores`.

## [0.3.1] - 2026-07-23

### Added
- `synapse --version` reports the installed distribution version.
- Windows support for the detached watch daemon: `synapse watch start` uses
  Windows process-creation flags instead of POSIX sessions on `win32`, and CI
  now runs the full test suite on Windows (Python 3.12).
- Tag-triggered release workflow: pushing a `v*` tag runs the full gate chain,
  verifies the tag matches the project version, rebuilds and smoke-tests the
  wheel, publishes to PyPI via trusted publishing (OIDC), then creates a GitHub
  Release from the matching changelog section and attaches the verified sdist.

### Changed
- Grammar downloads are now explicit through `synapse grammars install`. Indexing
  only loads parsers already present in the local cache and reports a concise setup
  error instead of performing a hidden network request.
- Distribution renamed from `synapse-mcp` to `locker-room-tools-synapse-mcp`;
  the import package (`synapse`) and the `synapse` CLI entry point are
  unchanged.
- `core.index` decomposed into focused modules: `index_schema` (DDL,
  connection lifecycle, atomic replacement), `index_writes` (connection-explicit
  writes), `index_queries` (read projections), and `reference_reconciliation`
  (shared by batch indexing and the watch worker). `SymbolIndex` remains as a
  stable facade; no public API changes.
- MCP server startup and watch commands validate the workspace path up front
  (`require_workspace_path`) instead of creating cache state for nonexistent
  directories.
- Qualified names now use each language's native scope separator (`::` for
  C++/CUDA/Crystal/Perl/Ruby/Rust, `\` for PHP) instead of a hardcoded `.`,
  and reference suffix matching follows the same separator.
- The ALL-CAPS-means-constant heuristic is disabled for case-insensitive and
  uppercase-idiomatic languages (Ada, Assembly, COBOL, Erlang, Fortran,
  Pascal, SQL, Verilog, VHDL), where it misclassified ordinary variables.

### Fixed
- Windows CI runs pytest through the synchronized virtual environment directly,
  with fault handling and full interrupt traces enabled.
- Windows CI installs and validates all native tree-sitter parsers before pytest,
  avoiding first-use extraction races and native loader interrupts during tests.

## [0.3.0] - 2026-07-13

### Added
- FTS5-backed symbol search: prefix matches use a full-text index kept in
  sync by triggers, with a substring fallback; existing databases migrate
  automatically via `PRAGMA user_version`.
- Angular component templates (`*.component.html`) are now detected and
  indexed via the `angular_template` queries.
- `failed_files` count in index stats: unreadable files (dangling symlinks,
  permission errors) are skipped with a warning instead of aborting the run.
- CI (GitHub Actions): lint/type/test matrix on Python 3.12-3.14 plus a
  packaging smoke job that installs the built wheel into a clean venv.

### Fixed
- Wheel now ships tree-sitter query files and adapter data: `queries/` and
  `adapters/` moved into the `synapse` package and are loaded via
  `importlib.resources`. Previous wheels failed with `FileNotFoundError` on
  every parse when installed from PyPI/pip.
- `synapse.__version__` is read from distribution metadata instead of a
  hardcoded, stale string; `synapse doctor` now reports the real version.

### Changed
- Minimum supported Python lowered from 3.14 to 3.12.
- PyPI metadata completed: license (MIT), authors, readme, classifiers,
  project URLs.

## [0.2.4] - 2026-07-12

### Added
- Initial public layout: tree-sitter based indexing across 67 languages,
  SQLite symbol index, 13 MCP tools, polling watch daemon, CLI
  (`index`, `setup`, `serve`, `watch`, `mcp install`, `uninstall`, `doctor`,
  `config`), and adapters for Claude Code, Codex, and OpenCode.
