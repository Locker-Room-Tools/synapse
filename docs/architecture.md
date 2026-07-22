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

- **`core.models`**: the normalized idiomatic symbol model, provenance metadata, and relation types.
- **`core.languages`**: supported language registry, extension detection, and tree-sitter naming.
- **`core.grammars`**: loads only parser binaries already present in the local cache;
  implicit downloads are rejected at the core boundary.
- **`core.parser`**: parses files with tree-sitter and maps captures to symbols.
- **`core.queries`**: resolves `.scm` query files by language and query name.
- **`core.workspace`**: derives per-workspace storage paths and persists workspace metadata.
- **`core.index`**: stable index facade; schema/lifecycle, writes, and read projections live in focused `core.index_*` modules.
- **`core.indexing`**: orchestrates crawl → hash → parse → upsert incremental indexing.
- **`core.crawler`**: discovers indexable files and hashes them for incremental indexing.
- **`mcp.server` / `mcp.tools`**: expose deterministic, token-frugal tools to agents
  (`synapse_index_workspace`, `synapse_search_symbols`, `synapse_get_definition`,
  `synapse_get_file_outline`, `synapse_get_symbol_context`, `synapse_get_dependencies`,
  `synapse_workspace_stats`, `synapse_project_map`, `synapse_get_file_dependencies`,
  `synapse_find_references`, `synapse_related_symbols`, `synapse_compact_context`,
  `synapse_watch_status`).
  Relations populated during indexing are `CONTAINS` (resolved member edges), `IMPORTS`
  (unresolved import target name), and `REFERENCES` (resolved or confidence-marked usage
  edges from reference queries).
- **`cli`**: provides `index`, `setup`, `serve`, `grammars install`, `mcp install`, and
  `uninstall` commands. Grammar installation is the explicit network-enabled setup step.
- **`adapters`** (`src/synapse/adapters/`, packaged data): provides agent-specific metadata and instruction snippets.
- **`cli.installer`**: owns reversible MCP client config writes. JSON configs are merged
  structurally, and Codex TOML config is managed with a marker block so uninstall removes
  only Synapse-owned content.

## Agent adoption layers

Synapse uses three default layers to make agents reach for structural context first:

1. MCP server instructions are advertised during the MCP handshake.
2. Entry-tool docstrings describe when to prefer Synapse over grep or file reads.
3. Optional repository instruction snippets add the same flow to agent rule files.

Skills are intentionally not part of the default install contract yet. They can be added
later per adapter once each client's skill location and trigger behavior are confirmed.

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
