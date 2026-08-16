## Synapse code context

Codebases on this machine are indexed by Synapse (MCP server `synapse`). For any code
question, list the evidence facets the task needs (including requested deliverables),
translate them into repository vocabulary (identifiers, file names, path fragments),
then:

- `synapse_orient(terms=[...])` -> 4-8 discriminative terms; ranked production-first
  matches with compact handles, weak candidates, crowded/unmatched terms, and coverage
  (no terms -> repository map)
- `synapse_inspect(symbols=[...handles...])` -> 2-3 initial facet-diverse anchors:
  definitions, bounded source, call-proven `callers`/`callees` plus neutral
  `refs_in`/`refs_out`, each with stored resolution, confidence, usage kind; then follow
  1-2 returned relation handles for facets still open

Both tools initialize and refresh the workspace automatically and bound their own
responses; there is no budget parameter to raise. Two calls are the common fast path,
not a cap; a weak orientation may need one more `synapse_orient` with better terms. Empty or truncated
results include a coverage block and are never proof of absence — empty `callers` means
no call was proven, not that none exist. If Synapse tools are deferred, load them
together in a single ToolSearch call before exploring. Treat returned source as read:
afterwards, use grep or file reads only to close a named partial/missing facet — an
exact text match, or an unindexed gap the coverage block reports — never to repeat the
whole investigation.
The `synapse-code-context` skill carries the full facet-planning, ledger, and
reliability-claim workflow.
