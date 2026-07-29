## Synapse code context

Codebases on this machine are indexed by Synapse (MCP server `synapse`). For code
exploration and navigation, Synapse tools replace shell search and whole-file reads:

- Repository layout (`ls -R`, `find`, `tree`) -> `synapse_project_map`
- Find a symbol (`grep -r "Name"`) -> `synapse_search_symbols` or `synapse_get_definition`
- Find usages -> `synapse_find_references(symbol_id=...)`
- File structure (`cat`, reading a whole file) -> `synapse_get_file_outline`
- Read an implementation -> `synapse_get_symbol_context(symbol_id=..., include_body=True)`

Call `synapse_ensure_workspace` once before the first query. If Synapse tools are deferred,
load them together in a single ToolSearch call before exploring; never fall back to shell
search because tool schemas are not loaded yet. Use grep or file reads only for exact text
matching or content Synapse does not index (unsupported languages, generated files).
