## Synapse Context Engine (use first)

This repository is indexed by Synapse. Prefer the two Synapse MCP navigation tools over
grep, ripgrep, shell search, and whole-file reads; they initialize the workspace
automatically.

Canonical flow:

1. Translate the user's request into likely repository vocabulary: identifiers, file
   names, path fragments (including translations of non-English task words).
2. `synapse_orient(terms=[...])` -> ranked production-first matches with compact handles
   (`s_...`), match provenance, weak candidates, crowded/unmatched terms, and coverage.
   No terms -> repository-map orientation (areas, entrypoints, anchors).
3. `synapse_inspect(symbols=[...handles...])` -> one batch call for the selected handles:
   definitions with `file:line`, bounded source slices, parent/children, and four
   relation groups — `callers`/`callees` (call-proven sites only) and
   `refs_in`/`refs_out` (every other reference) — with stored resolution
   (`exact|scoped|unique-name|ambiguous|unresolved`), confidence, and usage kind
   verbatim, plus unresolved hypotheses.
4. Synthesize the answer from that evidence. Two calls is the normal target; refine
   with one more `synapse_orient` (better terms or `path_scope`) when alignment is weak.

Empty or truncated results carry a coverage block and are never proof of absence, and a
complete payload is not a claim the evidence is complete. If Synapse tools are deferred,
load them together in one ToolSearch call before exploring. Use grep or file reads only
for exact text matching or gaps the coverage block reports (unsupported languages,
generated files). Administrative and primitive tools (re-indexing, definitions,
references, configuration, diagnostics) are available via `synapse serve --profile full`.

Validate the setup with `synapse doctor --path . --agent {agent_id}`.
