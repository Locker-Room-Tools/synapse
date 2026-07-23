## Synapse code context

Before exploring or navigating code, call `synapse_ensure_workspace`, then use Synapse MCP
tools before grep, shell search, or reading whole files. Fall back to exact-text search or
file reads only when exact text is required or Synapse does not index the required
information.
