## Synapse code context

Codebases on this machine are indexed by Synapse (MCP server `synapse`). For any code
question, translate the task into repository vocabulary (identifiers, file names, path
fragments), then:

- `synapse_orient(terms=[...])` -> ranked production-first matches with compact handles,
  weak candidates, crowded/unmatched terms, and coverage (no terms -> repository map)
- `synapse_inspect(symbols=[...handles...])` -> one batch call: definitions, bounded
  source, call-proven `callers`/`callees` plus neutral `refs_in`/`refs_out`, each with
  stored resolution, confidence, and usage kind

Both tools initialize and refresh the workspace automatically. Two calls is the normal
flow; a weak orientation may need one more `synapse_orient` with better terms. Empty or
truncated results include a coverage block and are never proof of absence — empty
`callers` means no call was proven, not that none exist. If Synapse tools are
deferred, load them together in a single ToolSearch call before exploring. Use grep or
file reads only for exact text matching or gaps the coverage block reports as unindexed.
