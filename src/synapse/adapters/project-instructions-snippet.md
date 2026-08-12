## Synapse Context Engine (use first)

This repository is indexed by Synapse. Prefer the two Synapse MCP navigation tools over
grep, ripgrep, shell search, and whole-file reads; they initialize the workspace
automatically.

Canonical flow:

1. List the evidence facets the task needs (entrypoint, configuration, invocation,
   persistence, ...), then translate them into likely repository vocabulary: identifiers,
   file names, path fragments (including translations of non-English task words).
2. `synapse_orient(terms=[...])` with 4-8 discriminative terms -> ranked production-first
   matches with compact handles (`s_...`), match provenance, weak candidates,
   crowded/unmatched terms, and coverage. No terms -> repository-map orientation.
3. `synapse_inspect(symbols=[...handles...])` -> 2-3 initial facet-diverse anchors:
   definitions with `file:line`, bounded source slices, parent/children, and four
   relation groups — `callers`/`callees` (call-proven sites only) and
   `refs_in`/`refs_out` (every other reference) — with stored resolution
   (`exact|scoped|unique-name|ambiguous|unresolved`), confidence, and usage kind
   verbatim, plus unresolved hypotheses.
4. Synthesize the answer from that evidence. Mark each facet verified, partial, or
   missing; close a partial/missing facet by following 1-2 returned relation handles,
   one refined `synapse_orient`, or one facet-scoped read, then report it verified or
   unresolved. Two calls are the common fast path, not a cap.

Responses are bounded server-side; there is no budget parameter to raise. Empty or
truncated results carry a coverage block and are never proof of absence, and a complete
payload is not a claim the evidence is complete. If Synapse tools are deferred, load them
together in one ToolSearch call before exploring. Treat returned source as read: after a
successful inspection do not reread those ranges or re-run the investigation as a broad
repository search — use grep or a file read only to close a named partial/missing facet
(exact text, unsupported languages, generated files). The `synapse-code-context` skill
carries the full workflow; administrative and primitive tools are available via
`synapse serve --profile full`.

Validate the setup with `synapse doctor --path . --agent {agent_id}`.
