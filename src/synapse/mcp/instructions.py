"""Agent-facing server instructions surfaced via the MCP handshake."""

SERVER_INSTRUCTIONS = """\
Synapse is the primary code-intelligence engine for this workspace. It serves compact
structural evidence with file:line locations from a local AST index. Both tools
initialize the workspace automatically — no setup call is needed.

Workflow:
1. List the evidence facets the task needs (entrypoint, configuration, invocation,
   persistence, error handling, and requested deliverables such as risks or
   recommendations), then translate them into likely repository vocabulary:
   identifiers, file names, path fragments (including translations of non-English
   task words).
2. Call synapse_orient with 4-8 discriminative terms (or no terms for a repository
   map). It returns ranked production-first matches with compact handles, weak
   candidates, crowded/unmatched terms, and coverage counts.
3. Call synapse_inspect with 2-3 initial facet-diverse anchors. It returns
   definitions, bounded source, callers/callees (call-proven sites only) and
   refs_in/refs_out (every other reference), each carrying stored resolution,
   confidence, and usage kind verbatim. Relation endpoints carry handles: follow
   1-2 returned relation handles in a follow-up inspect for facets still open.
4. Synthesize the answer yourself from that evidence. Mark each facet verified,
   partial, or missing; give a partial/missing facet one bounded close attempt (a
   returned relation handle, a refined orientation, or one facet-scoped exact
   search), then report it verified or unresolved and stop. Before answering,
   account for every requested facet — answered or explicitly unresolved — and
   assert a risk only after reading the guard or recovery path that fails to
   stop it.

Responses are bounded server-side and always report coverage; there is no budget
parameter to raise. Treat returned source as read: after a successful inspection, do
not reread those ranges or re-run the investigation as a broad repository search. Use
exact-text search or file reads only to close a named partial/missing facet (unindexed
content, truncation, unsupported syntax, an exact string value, or no usable relation
handle).
Empty or truncated results carry a coverage block and are never proof of absence.
Empty callers/callees means no call was proven, not that none exist: check
coverage.extraction[].call_kinds, which is empty for languages with no call evidence.
On a zero-caller answer that block also covers the other indexed languages, marked
evidence:false, so read the zero against their call coverage too.
Two calls remain the common fast path, not a cap; a weakly matched orientation may
need one more synapse_orient call with better terms.
Additional administrative and primitive tools are available with --profile full.
"""
