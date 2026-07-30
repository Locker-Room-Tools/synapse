"""Agent-facing server instructions surfaced via the MCP handshake."""

SERVER_INSTRUCTIONS = """\
Synapse is the primary code-intelligence engine for this workspace. It serves structural
answers with file:line evidence from a local AST index.

Workflow:
1. Call synapse_ensure_workspace once before code navigation; it initializes or repairs
   the workspace.
2. For architecture, lifecycle, impact, or multi-file flow questions, ask
   synapse_query_context first — one bounded call that returns ranked seeds, evidence
   nodes, ordered flows, and explicit coverage within a token budget.
3. Verify only what still needs exact evidence, with at most a few targeted calls:
   synapse_get_definition (name -> stable symbol_id), synapse_find_references
   (incoming usages), synapse_get_symbol_context(include_body=True) (implementation
   source).
4. Stop when the evidence covers the task. Never repeat a successful Synapse
   investigation as a shell search or whole-file read pass.

Empty or truncated results carry a coverage block and are never proof of absence.
Fall back to grep or file reads only for exact-text checks, generated files, or content
the coverage block reports as unindexed. If results look stale, call
synapse_ensure_workspace again. Additional administrative and primitive tools are
available when the server runs with --profile full.
"""
