---
name: synapse-code-context
description: Navigate and understand codebases with fresh Synapse structural context. Use for code exploration, architecture questions, execution flows, impact analysis, symbol lookup, definitions, and references before falling back to grep or whole-file reads.
---

<!-- SYNAPSE MANAGED SKILL -->

# Synapse Code Context

Use Synapse as the first source of code structure. One bounded context query answers most
architecture questions; follow it with a few targeted checks and stop.

## Workflow

1. Before the first code-navigation operation:
   - Normally call `synapse_ensure_workspace`. Continue when it reports `initialized: true`,
     `daemon.running: true`, and `daemon.degraded: false`.
   - If the task explicitly forbids workspace mutation, use query tools only if a previous
     call in this session succeeded; otherwise explain that initialization needs permission
     and use a permitted fallback.
2. Ask `synapse_query_context(question=...)` for architecture, lifecycle, impact, or
   multi-file flow questions. It returns ranked seeds, evidence nodes with `file:line`,
   resolution and confidence, ordered flows, and an explicit coverage block, all within
   `token_budget`. Narrow with `symbol_ids`, `direction` (`in`/`out`/`both`), or
   `max_depth` when the first answer is too broad.
3. Verify only what still needs exact evidence (at most a few calls):
   - Exact declaration: `synapse_get_definition(name=...)` -> stable `symbol_id`.
   - Usages: `synapse_find_references(symbol_id=...)`.
   - Implementation source: `synapse_get_symbol_context(symbol_id=..., include_body=True)`;
     when `body_truncated` is true, narrow to a child symbol or raise `max_body_lines`.
4. Stop when the evidence covers the task. Never repeat a successful Synapse investigation
   as a full shell-search or file-read pass.
5. Read the `coverage` and `truncation` blocks: empty or truncated results are never proof
   of absence. Use grep or file reads only for exact text, generated files, unsupported
   syntax, or gaps the coverage block reports.

## Freshness and recovery

If a query reports that the workspace is uninitialized or degraded, call
`synapse_ensure_workspace` again unless the task forbids mutation. If Synapse tools are
unavailable, report that the global integration may need installation or an agent restart;
do not imitate those operations manually. Administrative tools (re-indexing, configuration,
watch diagnostics) live behind `synapse serve --profile full`.
