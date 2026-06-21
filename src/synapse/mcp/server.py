"""FastMCP server skeleton for Synapse."""

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from synapse.mcp.workspace import configure_workspace

mcp = FastMCP("synapse")


def run(workspace_path: str | Path = ".") -> None:
    """Run the Synapse MCP server over stdio."""
    configure_workspace(workspace_path)
    from synapse.mcp import tools

    _ = tools
    mcp.run(transport="stdio")
