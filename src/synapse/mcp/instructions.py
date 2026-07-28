"""Agent-facing server instructions surfaced via the MCP handshake."""

SERVER_INSTRUCTIONS = """\
Synapse is the primary code-intelligence engine for this workspace. It serves structural
answers from a local AST index.

Before code navigation, call synapse_ensure_workspace. It lazily initializes new workspaces
and repairs index freshness or daemon health when needed.

Prefer Synapse tools BEFORE grep/ripgrep/shell search or reading whole files:
- Find symbols -> synapse_search_symbols
- Locate a declaration -> synapse_get_definition (returns a stable symbol_id)
- Find usages -> synapse_find_references(symbol_id=...)
- Understand a file before reading it -> synapse_get_file_outline
- Understand a symbol -> synapse_compact_context / synapse_get_symbol_context
- Broad architecture -> synapse_project_map; index stats -> synapse_workspace_stats
- Relations -> synapse_get_dependencies (outgoing), synapse_get_file_dependencies
  (file imports), synapse_related_symbols (neighbors)
- Change what Synapse indexes -> synapse_get_config, then
  synapse_add_ignored_directories / synapse_remove_ignored_directories

Canonical flow: synapse_get_definition(name=...) -> synapse_find_references(symbol_id=...).
Only fall back to grep/file reads when a symbol is not indexed or you need exact text.
Configure Synapse only through these tools; never hand-edit Synapse config files.
If results look stale, call synapse_ensure_workspace again and inspect synapse_watch_status.
"""
