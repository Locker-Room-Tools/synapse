---
name: add-mcp-tool
description: Add or change a Synapse MCP tool while keeping the profile, budget, and coverage contracts. Use when asked to expose new functionality over MCP, change a tool's signature or docstring, or move a tool between profiles.
---

# Add or change an MCP tool

## 0. Design gate — does this tool deserve to exist?

The surface optimizes total task tokens and turns, not single-response size. Before adding a
tool, prefer extending one bounded high-level core operation (search + traversal + ranking +
projection server-side) over adding another small primitive; many compact sequential calls cost
more context than one bounded response. Core logic lives in `src/synapse/core`; `mcp` is a thin
presentation layer and never contains logic; a tool never calls another tool.

## 1. Anatomy

Implement the operation in `core` first, then add a 1–5-line delegator in
`src/synapse/mcp/tools.py` decorated with `@tool(...)` from `synapse/mcp/profiles.py`:

- Registration is a decorator side effect; declaration order is registration order; the wire
  name is `func.__name__`, which `cli/doctor.expected_tools()` also consumes — no second list.
- `@tool()` registers to the FULL profile; `@tool(ToolProfile.DEFAULT, ...)` to both. The
  DEFAULT profile is contractually the two-call navigation surface — adding to it is a major
  design decision, not a convenience.
- The docstring **is** the wire contract the agent reads. First line = summary, body = contract.
  Tests assert exact phrases in docstrings; write them deliberately.
- Parameters are flat scalars/lists with defaults; `workspace_path: str = "."` comes last.
- Workspace access: `_workspace_index(path)` for full-profile query tools (rejects an
  uninitialized workspace via `require_workspace_ready`); `_navigation_workspace(path)` for
  lazy-init navigation tools (readiness decisions live entirely in `core.lifecycle`).
- Query tools return dicts, never `None`; misses use the `_not_found()` envelope
  (`{"found": False, "target", "reason", "hint"}`). Argument validation raises `ValueError`.
- Navigation-style tools return one budget-bound JSON string and pass
  `structured_output=False` so the payload is serialized once, and they expose **no**
  `token_budget` parameter. Budgets live in `src/synapse/core/navigation/budget.py`
  (orient 800, inspect 2400, public ceiling 4000; `enforce_budget` applies a hard character cap
  with deterministic drop steps).

## 2. Coverage rule

Every bounded, paginated, truncated, ambiguous, heuristic, or empty result must make its
coverage explicit. An empty result must never be readable as proof of absence, and a truncated
one must never look complete. If the new tool bounds anything, its payload carries a coverage
block and (for navigation-style payloads) `payload_complete`.

## 3. Test checklist (`tests/mcp/`)

- `test_profiles.py` — bump the hard count `assert len(full) == 19`; DEFAULT set equality if the
  default surface changes (it almost never should).
- `test_tools.py` — add: a delegation test (monkeypatch the workspace helper and the core call,
  assert the exact request/return), `_FakeIndex`/`_EmptyIndex` support, a docstring-phrase
  assertion in the contract tests, an entry in the parametrized
  `test_query_tools_require_lazy_workspace_initialization` list, and (if the tool can miss) the
  uniform not-found envelope aggregation.
- `test_schema_budget.py` — only if touching DEFAULT: the exact-two-tools assertion breaks, and
  the serialized `{name, description, inputSchema}` surface must stay ≤ 800 estimated tokens, so
  the docstring budget is shared.
- `test_server.py` — only if the server instructions text must mention the tool.

## 4. Docs

- `docs/tools.md` — per-tool `### synapse_x` section.
- `AGENTS.md` — the full-profile roster and the "(19 tools total)" count.
- `docs/architecture.md` — the tool roster line.
- `CHANGELOG.md` — entry under `## [Unreleased]`.

## 5. Gate

```bash
uv run ruff check && uv run mypy && uv run pytest -q tests/mcp
```
