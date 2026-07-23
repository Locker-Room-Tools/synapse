---
name: synapse-code-context
description: Navigate and understand codebases with fresh Synapse structural context. Use for code exploration, architecture questions, symbol lookup, definitions, references, dependencies, file outlines, project maps, and compact code context before falling back to grep or whole-file reads.
---

<!-- SYNAPSE MANAGED SKILL -->

# Synapse Code Context

Use Synapse as the first source of code structure. Keep calls bounded and reuse stable
`symbol_id` values between tools.

## Workflow

1. Before the first code-navigation operation:
   - Normally call `synapse_ensure_workspace`. Continue when it reports `initialized: true`,
     `daemon.running: true`, and `daemon.degraded: false`.
   - If the task explicitly forbids workspace mutation, call the read-only
     `synapse_watch_status` instead. Use query tools only when it reports
     `initialized: true`, `running: true`, and `degraded: false`; otherwise explain that
     initialization needs permission and use a permitted fallback.
2. Choose the narrowest structural entry point:
   - Known symbol: `synapse_get_definition(name=...)`.
   - Unknown symbol: `synapse_search_symbols(query=...)`.
   - Known file: `synapse_get_file_outline(file_path=...)`.
   - Broad architecture: `synapse_project_map`.
3. Reuse the returned `symbol_id`:
   - Usages: `synapse_find_references(symbol_id=...)`.
   - Local structure: `synapse_get_symbol_context` or `synapse_compact_context`.
   - Relations: `synapse_get_dependencies`, `synapse_get_file_dependencies` (file
     imports), or `synapse_related_symbols`.
4. Follow pagination metadata instead of requesting unbounded results.
5. Use grep or file reads only for exact text, generated files, unsupported syntax, or details
   explicitly absent from the index.

## Freshness and recovery

If a query reports that the workspace is uninitialized or degraded, call
`synapse_ensure_workspace` again unless the task forbids mutation. Inspect
`synapse_watch_status` only when freshness or daemon health needs diagnosis. If Synapse tools
are unavailable, report that the global integration may need installation or an agent
restart; do not imitate those operations manually. Do not implement installation or daemon
lifecycle inside the skill. Use a permitted bounded fallback when continuing without Synapse
is useful.
