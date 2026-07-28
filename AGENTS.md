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
- Tools are deterministic and token-frugal. Typed params/returns; concise docstrings
  (the docstring is the agent-facing contract). Return structural data, not prose.
- Current tools: `synapse_ensure_workspace`, `synapse_index_workspace`, `synapse_search_symbols`,
  `synapse_get_definition`, `synapse_get_file_outline`, `synapse_get_symbol_context`,
  `synapse_get_dependencies`, `synapse_workspace_stats`, `synapse_project_map`,
  `synapse_get_file_dependencies`, `synapse_find_references`,
  `synapse_related_symbols`, `synapse_compact_context`, `synapse_watch_status`,
  `synapse_get_config`, `synapse_add_ignored_directories`,
  `synapse_remove_ignored_directories`.

### Ideal Agent Flow

```
synapse_ensure_workspace()
synapse_get_definition(name="synapse_find_references")
→ { symbol_id: "...", file_path: "...", line_range: [...] }

synapse_find_references(symbol_id="...")
→ { items: [...], files: [...] }
```

Avoid intermediate `search_symbols` or manual `grep` when a `symbol_id` is available.

## Commits / PRs
- Do not commit `.venv/`, `__pycache__/`, `.idea/`, or `.ai/` output.
- Small, focused commits; conventional style (`feat:`, `fix:`, `docs:`, `refactor:`).

## Do NOT
- Upload code anywhere or add network calls in the index path (local-first guarantee).
- Edit `pyproject.toml` deps by hand — use `uv add` / `uv remove`.
