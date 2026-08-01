"""Agent-facing server instructions surfaced via the MCP handshake."""

SERVER_INSTRUCTIONS = """\
Synapse is the primary code-intelligence engine for this workspace. It serves compact
structural evidence with file:line locations from a local AST index. Both tools
initialize the workspace automatically — no setup call is needed.

Workflow:
1. Translate the user's request into likely repository vocabulary: identifiers,
   file names, path fragments (including translations of non-English task words).
2. Call synapse_orient with those terms (or no terms for a repository map). It
   returns ranked production-first matches with compact handles, weak candidates,
   crowded/unmatched terms, and coverage counts.
3. Select the handles the task needs and call synapse_inspect once with several of
   them. It returns definitions, bounded source, callers/callees (call-proven sites
   only) and refs_in/refs_out (every other reference), each carrying stored
   resolution, confidence, and usage kind verbatim.
4. Synthesize the answer yourself from that evidence. Use exact-text search or file
   reads only for gaps the coverage block reports (unindexed content, truncation,
   unsupported syntax).

Empty or truncated results carry a coverage block and are never proof of absence.
Empty callers/callees means no call was proven, not that none exist: check
coverage.extraction[].call_kinds, which is empty for languages with no call evidence.
On a zero-caller answer that block also covers the other indexed languages, marked
evidence:false, so read the zero against their call coverage too.
A weakly matched orientation may need one more synapse_orient call with better terms.
Additional administrative and primitive tools are available with --profile full.
"""
