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
- **`core.config`**: `settings` resolves configuration across three layers — packaged defaults, the
  global user config, and a workspace-local `.synapse/config.json` — and owns atomic writes.
  `ignores` is the shared directory ignore matcher used by both the crawler and the watch layer,
  so crawl and watch filter identically.
- **`core.context`**: the high-level retrieval pipeline behind `synapse_query_context`:
  Unicode-aware deterministic keyword extraction (`keywords`), seed discovery with a
  structural fallback tier for identifier-free questions (`seeds`), bounded
  breadth-first traversal over stored relations with cycle prevention and hub-fan-out
  suppression (`traversal`), a deterministic output budget with explicit truncation
  metadata (`budget`), and orchestration plus coverage assembly (`query`).
  The whole read runs on one consistent SQLite snapshot via `SymbolIndex.read_session()`.
  Traversal trust policy: containment and exact/scoped references are transit edges;
  heuristic (unique-name) references stay leaf evidence and are never expanded, and
  flows are ranked by aggregate path trust before depth. The advertised token budget
  is an estimate at 4 chars/token; the hard guarantee is a character cap of
  `token_budget * 4` on the exact serialized result. Ranking may reorder stored facts
  (for example demoting test paths) but never upgrades stored confidence, and
  projected flows are marked as projections over stored edges; non-tree edges are
  projected separately with discovered/projected/omitted accounting.
- **`mcp.server` / `mcp.tools` / `mcp.profiles`**: expose deterministic, token-frugal
  tools to agents through profile-tiered registration (`synapse serve --profile
  default|full`). The default profile is the minimal coding-agent surface
  (`synapse_ensure_workspace`, `synapse_query_context`, `synapse_get_definition`,
  `synapse_get_symbol_context`, `synapse_find_references`); the full profile adds
  (`synapse_index_workspace`, `synapse_search_symbols`, `synapse_get_file_outline`,
  `synapse_get_dependencies`,
  `synapse_workspace_stats`, `synapse_project_map`, `synapse_get_file_dependencies`,
  `synapse_related_symbols`, `synapse_compact_context`,
  `synapse_watch_status`, `synapse_get_config`, `synapse_add_ignored_directories`,
  `synapse_remove_ignored_directories`).
  `synapse_query_context` returns one compact JSON string so its token budget binds the
  exact wire payload; query tools return a uniform `{found: false, ...}` envelope
  instead of `None` so not-found never serializes as an empty result.
  Relations populated during indexing are `CONTAINS` (resolved member edges), `IMPORTS`
  (unresolved import target name), and `REFERENCES` (usage edges from reference queries).
  Reference resolution is syntactic and structural, never semantic.
  `core.indexing.resolution` binds a reference as `exact` only when the source syntax plus
  the indexed declarations prove one target — a fully-qualified name, an unambiguous dotted
  suffix, or a member reached through a receiver whose type is declared in the source.
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
- **`core.provenance`**: reports which Synapse build is serving a call — version, the
  directory the package was imported from, schema/extractor versions, the reference
  fingerprint, and PEP 610 editability. Surfaced additively on `ensure_workspace`,
  `workspace_stats`, and `synapse status`, so a stale globally-installed tool is
  distinguishable from a live checkout. Installation identity only; no environment data.
- **`cli`**: provides global `install`, workspace `init`/`status`, project-scoped `setup`,
  `serve`, grammar, watch, doctor, and uninstall commands. Global install is the canonical
  onboarding path; project setup remains an advanced compatibility path.
- **`cli.config`**: `synapse config ignored-dirs list|add|remove`, scoped with
  `--scope project|global`; delegates every write to `core.config`.
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

## Configuration layering

Ignored directories resolve as a **union, not an override**: packaged defaults ∪ global user
config (`~/.config/synapse/config.json`) ∪ project config (`<workspace>/.synapse/config.json`).
A built-in entry therefore cannot be un-ignored by a lower layer, and every effective entry
reports each layer that contributes it.

Entries are gitignore-compatible: a bare name (`node_modules`) matches at any depth, while a
leading or embedded slash anchors to the workspace root (`/build`, `src/generated`). Globs are
not supported.

Only `ignored_directories` is agent-writable, and only in the project layer. The `watch.*`
tunables stay global and CLI-only. Because `core.config` is re-read on every crawl, an ignore
change converges on the next sweep without a reindex or daemon restart.

The project config is meant to be committed, so the whole team indexes the same tree.

## Agent adoption layers

Synapse uses five global layers to make agents reach for structural context first:

1. A marker-managed global instruction requires `synapse_ensure_workspace` before code
   navigation.
2. MCP server instructions advertise the same bootstrap and Synapse-first flow.
3. Entry-tool docstrings describe when to prefer structural tools over text search.
4. The managed `synapse-code-context` skill supplies the detailed multi-tool workflow.
5. For Claude Code, a suggest-only `PreToolUse` hook (`synapse hook claude-pre-bash`)
   injects a Synapse reminder when shell exploration commands run inside an indexed
   workspace. It never blocks or auto-approves the command and returns only
   `additionalContext`.

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
3. `synapse_ensure_workspace` installs parsers, builds the initial index, and starts the
   detached polling daemon, which becomes the incremental index writer for that workspace.
4. Initialized MCP sessions restore a missing daemon; query tools reject uninitialized or
   degraded state instead of exposing an empty or stale index.

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
