# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Decorated Python definitions (`@decorator def …`, decorated methods, decorated
  async defs) are now indexed as their actual function declarations; previously the
  anchored tree-sitter patterns missed `decorated_definition` wrappers, which made
  every FastMCP-decorated tool invisible to search and mis-anchored the references in
  their bodies. The reference-extraction fingerprint changes, so existing indexes
  rebuild automatically.
- `synapse_query_context` traversal no longer expands heuristic (`unique-name`)
  references: they are returned as leaf evidence with full resolution/confidence
  metadata, so unrelated symbols sharing a generic name (`add`, `get`, `run`) can no
  longer be chained into a false multi-hop flow. Flows now carry an aggregate
  `trust` label (`exact`/`scoped`/`heuristic`, the weakest edge on the path) and are
  ranked by trust before depth.
- The context-query output bound is now a hard, tested guarantee: the returned
  string never exceeds `token_budget * 4` characters (the token figure itself is an
  estimate), user-controlled echoes (question, symbol id lists) are bounded, and the
  fallback envelopes are themselves bounded. Previously a large question could push
  the minimal envelope past the cap.
- Non-tree graph edges (cross-links, cycles) are no longer silently dropped from the
  projection: a bounded ranked `edges` section returns them with evidence, and
  `coverage.projection.edges` accounts for discovered vs tree-projected vs
  extra-projected vs omitted edges.
- Seed-level references the index cannot bind to one target (`ambiguous`/
  `unresolved`) now surface in a bounded `unresolved` section with name, stored
  resolution, and `file:line` site — a gap that invites a targeted
  `synapse_get_definition` drill-down instead of masquerading as absence.
- Questions with no ASCII identifiers (including non-English questions) no longer
  dead-end in `no-seed-match`: tokenization is Unicode-aware, and a deterministic
  structural fallback seeds the query from connected production declarations across
  repository areas, reported via `coverage.seeds.origin`/`fallback_reason`.
  Question-matched seed sets consisting only of test code are flagged with
  `coverage.seeds.only_test_matches`.
- MCP and context tests no longer touch the user-global Synapse data directory:
  autouse fixtures isolate `SYNAPSE_DATA_DIR` per test.

### Added
- `synapse_query_context`: a high-level, deterministic, token-budgeted context query.
  One bounded MCP call performs seed discovery, multi-hop traversal over stored
  relations, ranking, deduplication, and evidence projection server-side (new
  `core/context` package), executed on one consistent SQLite read snapshot. Results are
  a single compact JSON string with ranked seeds, `file:line` evidence carrying stored
  resolution/confidence verbatim, ordered flows (marked as projections over stored
  edges), file-level imports, an explicit five-part coverage model (index, extraction,
  traversal, resolution, projection), and truncation metadata; seeds, the primary flow,
  and coverage always survive the budget.
- Profile-tiered MCP tool surface: `synapse serve --profile default|full`. The default
  profile exposes the minimal coding-agent set (`synapse_ensure_workspace`,
  `synapse_query_context`, `synapse_get_definition`, `synapse_get_symbol_context`,
  `synapse_find_references`); `--profile full` restores every tool. `synapse doctor`
  derives its expected tool sets from the same registry and probes the default surface
  exactly.

### Changed
- **Breaking:** bare `synapse serve` now serves the minimal default profile instead of
  all seventeen tools; pass `--profile full` for the previous surface. Installed agent
  configs pick the new default up on upgrade without config changes.
- Query tools that previously returned `null`/empty content for a missing symbol or
  file (`synapse_get_definition`, `synapse_get_file_outline`,
  `synapse_get_file_dependencies`, `synapse_get_symbol_context`,
  `synapse_related_symbols`, `synapse_compact_context`) now return a uniform
  `{found: false, target, reason, hint}` envelope, and no longer wrap structured
  results in `{"result": ...}`.
- Managed instructions, the server handshake, the `synapse-code-context` skill, and the
  Claude Code hook reminder now teach the bounded workflow: ensure workspace → one
  `synapse_query_context` → at most a few targeted evidence checks → stop, with an
  explicit rule against repeating a successful Synapse investigation through shell
  search.
- Nine new agent adapters for `synapse install` / `synapse uninstall` / `synapse setup`,
  each verified against official documentation on 2026-07-29: `hermes`, `gemini`, `copilot`,
  `cursor`, `windsurf`, `cline`, `kiro`, `qwen`, and `continue`. Capabilities are declared
  per agent and independently optional, so unsupported scopes fail with a capability-specific
  message and unsupported instructions or skills are skipped cleanly instead of guessed.
  See the support matrix in `docs/installation.md`, including the agents that were
  investigated and deliberately deferred with their exact blockers.
- YAML MCP configuration support, covering both container shapes in use: Hermes' mapping
  under `mcp_servers` and Continue's list under `mcpServers` matched on an inner `name`.
  Configs are round-tripped with `ruamel.yaml`, so user comments, key order, and
  anchor/alias pairs survive install and uninstall.
- Project-scoped skill installation for agents that document a project skill directory.
  `synapse setup` installs it alongside the project instruction file; `--no-skill` skips it
  and `synapse uninstall --keep-skill` retains it.
- Suggest-only Claude Code `PreToolUse` hook (`synapse hook claude-pre-bash`): when a
  shell exploration command runs in an indexed workspace, it injects a reminder to use
  Synapse tools via `additionalContext` without blocking or auto-approving the command.
  Installed by `synapse install claude-code` (skip with `--no-hook`; removed by
  `synapse uninstall claude-code --global`).
- C# reference queries now capture member accesses, generic type names and arguments,
  variable/parameter/return/property/field types (including qualified, nullable, and
  array forms), object creation, `typeof`, casts, `as`/`is` patterns, `catch` types,
  attributes, and base lists — `synapse_find_references` no longer returns false zeros
  for symbols used outside call positions.
- Honest reference resolution: relations carry a `resolution` marker (`unique-name`,
  `ambiguous`, `unresolved`) plus the reference site's `line`/`byte_column`;
  `synapse_find_references` returns confirmed `items` (`match: "heuristic"`) separately
  from same-name `possible_items` (ambiguous entries expose `candidate_symbol_ids`,
  `candidate_count`, `candidates_truncated`) and a `coverage` block (resolution model,
  per-language extraction completeness and limitation ids, match-kind counts,
  `zero_result` marker). A unique global name match is reported as a heuristic, not as
  semantically exact.
- Reference-extraction fingerprint stored in the index (`index_meta`): changing packaged
  `.scm` queries or extractor semantics now deterministically forces a full rebuild on
  the next `ensure_workspace`/`synapse index` instead of silently reusing stale
  relations.
- Per-language reference coverage metadata on `LanguageSpec`
  (`reference_extraction`, `reference_usage_kinds`, `reference_limitations`,
  `reference_syntax`).
- Conservative structural reference resolution. A reference is now bound as
  `resolution: "exact"` (`match: "exact"`, high confidence) only when the syntax plus the
  indexed declarations prove a single target: a fully-qualified name, an unambiguous
  dotted suffix, or a member reached through a receiver whose type is declared in the
  source — so `dbContext.Servers` resolves while `location.Servers` on an implicitly
  typed lambda parameter stays ambiguous. Namespace, import, and enclosing-type narrowing
  report the new, weaker `resolution: "scoped"` tier rather than claiming proof. The
  resolver is language-agnostic; C# grammar facts come from `LanguageSpec.reference_syntax`.
  No compiler, Roslyn, LSP, or network dependency is involved.
- Reference relations carry `usage_kind` (the advertised syntactic position of the usage)
  and `to_qualified_name` (the dotted name written at the site), both surfaced on
  `synapse_find_references` items.
- C# reference extraction now covers `nameof(...)` arguments, tuple element and tuple
  parameter types, `foreach` element types, `default(T)`, and usages in top-level
  statements (anchored to the file when no enclosing declaration exists). `nameof` itself
  is no longer reported as an invocation.
- Runtime provenance (`runtime`) on `synapse_ensure_workspace`, `synapse_workspace_stats`,
  and `synapse status`: package version, the directory the code was imported from, schema
  and extractor versions, the reference fingerprint, and whether the install is editable
  (PEP 610). This makes a stale globally-installed tool distinguishable from the checkout
  under development. No environment or secret data is exposed.
- C# symbol queries for file-scoped namespaces, local functions (covers
  top-level-statements `Program.cs`), delegates, event fields, and enum members.
- `signature` on every `synapse_get_file_outline` item.
- `max_body_lines` parameter (default 200) and a `body_truncated` flag on
  `synapse_get_symbol_context`, so implementation source is readable without
  unbounded dumps.
- Build-output directories in the default ignore list: `obj`, `bin`, `target`,
  `out`, `coverage`, `.vs`, `.gradle`, `.next`, `.nuxt`, `.tox`, `.pytest_cache`,
  `DerivedData`, `Pods`.

### Fixed
- `synapse install opencode` no longer adds a `$schema` key to an existing `opencode.json`
  that lacked one. Synapse seeded a key it never removed, so install followed by uninstall
  did not restore the original file.
- `synapse_project_map` excludes namespaces and imports from `top_symbols` entirely
  (delegates surface via the `type` kind) and aggregates deduplicated namespace names
  under `namespaces` (`items`, `total`, `truncated`), so per-file C# namespaces no
  longer crowd out or pad the declaration list.
- `synapse_find_references(symbol_id=...)` no longer presents every unresolved
  same-name reference as a confirmed usage of that symbol.
- `synapse_find_references` pages `possible_items` with the same `limit`/`offset` as
  confirmed `items` and reports a matching `possible_page` block, so ambiguous results
  beyond the first page are reachable instead of being re-served from the start on every
  request. Both collections are ordered by file path, line, byte column, then relation
  id.
- `ensure_workspace` stops a live watch daemon before a forced rebuild instead of
  crashing on the watch lock.
- A schema upgrade migrates the `relations` table in place; legacy rows read with
  empty locations until the fingerprint-triggered rebuild replaces them.
- C# file-scoped namespaces (`namespace X;`) now scope the rest of the file, so types
  declared under them carry fully-qualified names (`Overlock.Api.Servers.Server`) instead
  of bare ones. Previously the declaration ended at its semicolon and nothing nested
  under it.
- `synapse_find_references` reads the persisted `resolution` to classify ambiguous versus
  unresolved results, instead of recomputing it from whatever definitions happen to match
  at query time.
- `synapse_project_map` no longer returns a page of nothing but classes: `top_symbols`
  uses a deterministic relevance ranking with a per-kind cap, and reports
  `top_symbols_total` / `top_symbols_truncated`.

### Changed
- Agent adapters are now declarative. `cli/adapters.py` became the `cli.adapters` package
  (capability model, data-only registry, path resolution, instructions, skills, rendering)
  and format handling moved into `cli.config_codecs`, where JSON and YAML share one merge
  algorithm. Twelve agent-id conditionals were removed from generic install, uninstall,
  render, and path-resolution code. Claude Code, Codex, and OpenCode behaviour, including
  their serialized MCP config, is unchanged.
- Home-directory overrides are resolved per path rather than per agent. This is required by
  Cline, where `CLINE_DATA_DIR` replaces `~/.cline/data/` and therefore relocates the global
  rules and skill but not `~/.cline/mcp.json`. `CODEX_HOME` and `XDG_CONFIG_HOME` keep their
  existing behaviour.
- The three near-identical per-agent instruction snippets were replaced by one shared
  template rendered per agent.
- `synapse_find_references`'s `files` now lists every path in the whole result rather
  than only the current page; the page-scoped list moved to `page.files`.
- `synapse_find_references` `coverage.counts` gained `exact` and `scoped` keys and
  reports `heuristic` as unique-name matches only. `resolved` is retained as an alias for
  `exact`. `coverage.resolution_model` is now `"syntactic-structural"`.
- The advertised C# `reference_usage_kinds` gained `nameof`, and `typeof` was renamed to
  `type-literal` now that it also covers `default(T)`. The `reference_limitations` ids
  changed: the extraction gaps (`top-level-statements`, `nameof-arguments`,
  `nested-type-wrappers`, `lambda-and-tuple-types`) are closed and replaced by the
  remaining semantic ones (`static-receiver-types`, `extension-methods`,
  `inherited-members`, `partial-classes`).
- `synapse_project_map` nests the current page's file list under `page.files`;
  `top_symbols` and `namespaces` remain workspace-wide aggregates.
- `SCHEMA_VERSION` 3 → 4 and the reference extractor version 1 → 2. Existing workspaces
  perform one fingerprint-forced rebuild on the next `ensure_workspace`.
- Rewrote the managed agent instructions (global snippet, per-agent snippets, MCP
  server instructions, skill) around explicit shell-command-to-tool substitutions
  and the `include_body=True` read path, so agents stop falling back to
  `find`/`grep`/`cat` for indexed codebases.

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
