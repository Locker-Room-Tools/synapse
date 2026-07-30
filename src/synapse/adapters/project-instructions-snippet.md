## Synapse Context Engine (use first)

This repository is indexed by Synapse. Call `synapse_ensure_workspace` before the first
code navigation in a session, then prefer Synapse MCP tools over grep, ripgrep, shell
search, and whole-file reads.

Canonical flow:

1. `synapse_ensure_workspace()` -> initializes or repairs the workspace; continue when healthy.
2. `synapse_query_context(question=...)` -> one bounded answer for architecture, lifecycle,
   impact, and multi-file flow questions: ranked seeds, evidence nodes with `file:line`,
   ordered flows, and explicit coverage/truncation within a token budget.
3. At most a few targeted evidence checks:
   - `synapse_get_definition(name=...)` -> a stable `symbol_id` for an exact name.
   - `synapse_find_references(symbol_id=...)` -> incoming usages.
   - `synapse_get_symbol_context(symbol_id=..., include_body=True)` -> implementation source.
4. Stop when the evidence covers the task. Never repeat a successful Synapse investigation
   as a shell-search or file-read pass.

Empty or truncated results carry a coverage block and are never proof of absence — narrow
the question or pass explicit `symbol_ids` instead of falling back to shell. If Synapse
tools are deferred, load them together in one ToolSearch call before exploring. Use grep or
file reads only for exact text matching or content the coverage block reports as unindexed
(unsupported languages, generated files). Administrative tools (re-indexing, configuration,
diagnostics) are available via `synapse serve --profile full`.

Validate the setup with `synapse doctor --path . --agent {agent_id}`.
