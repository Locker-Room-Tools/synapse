# AGENTS.md — Working on Synapse

Synapse is a local-first, AST-based code context engine exposed to AI agents over MCP.
This file is the contract for any human or AI agent contributing to THIS repository.

## Setup
- Python >=3.14. Use `uv`: `uv venv && uv pip install -e ".[dev]"`.
- Run the MCP server (stdio): `python -m synapse` (or the `synapse` script).
- Remove managed agent setup with `synapse uninstall <agent> --path .`; this does not
  delete index/cache data.

## Project structure
- `src/synapse/core` — core logic: model, parsing, indexing, querying. No MCP imports.
- `src/synapse/mcp` — FastMCP presentation layer; thin, delegates to `core`.
- `src/synapse/cli` — CLI entrypoints for indexing, setup, and MCP install helpers.
- `src/synapse/adapters/` — agent-specific MCP config templates and instruction snippets (packaged data).
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
- Current tools: `synapse_index_workspace`, `synapse_search_symbols`,
  `synapse_get_definition`, `synapse_get_file_outline`, `synapse_get_symbol_context`,
  `synapse_get_dependencies`, `synapse_workspace_stats`, `synapse_project_map`,
  `synapse_get_file_dependencies`, `synapse_find_references`,
  `synapse_related_symbols`, `synapse_compact_context`.

### Ideal Agent Flow

```
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
