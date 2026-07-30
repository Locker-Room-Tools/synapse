## Synapse code context

Codebases on this machine are indexed by Synapse (MCP server `synapse`). Call
`synapse_ensure_workspace` once before the first query, then:

- Architecture, lifecycle, impact, or multi-file flow questions -> `synapse_query_context`
  (one bounded call: ranked evidence, ordered flows, file:line, explicit coverage)
- Locate a declaration -> `synapse_get_definition` (returns a stable `symbol_id`)
- Find usages -> `synapse_find_references(symbol_id=...)`
- Read an implementation -> `synapse_get_symbol_context(symbol_id=..., include_body=True)`

After a successful `synapse_query_context`, make at most a few targeted follow-up calls and
stop; do not repeat the investigation with shell search or whole-file reads. Empty or
truncated results include a coverage block and are never proof of absence. If Synapse tools
are deferred, load them together in a single ToolSearch call before exploring. Use grep or
file reads only for exact text matching or content the coverage block reports as unindexed.
