"""Agent-facing server instructions surfaced via the MCP handshake."""

SERVER_INSTRUCTIONS = """\
Synapse is the primary code-intelligence engine for this workspace. It serves structural
answers from a local AST index.

Prefer Synapse tools BEFORE grep/ripgrep/shell search or reading whole files:
- Find symbols -> synapse_search_symbols
- Locate a declaration -> synapse_get_definition (returns a stable symbol_id)
- Find usages -> synapse_find_references(symbol_id=...)
- Understand a file before reading it -> synapse_get_file_outline
- Understand a symbol -> synapse_compact_context / synapse_get_symbol_context

Canonical flow: synapse_get_definition(name=...) -> synapse_find_references(symbol_id=...).
Only fall back to grep/file reads when a symbol is not indexed or you need exact text.
If results look stale, check synapse_watch_status, then synapse_index_workspace.
"""
