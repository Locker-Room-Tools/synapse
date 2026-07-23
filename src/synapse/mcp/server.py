"""FastMCP server skeleton for Synapse."""

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from synapse.core.watch.daemon import ensure_watch_daemon
from synapse.core.workspace import read_metadata
from synapse.mcp.instructions import SERVER_INSTRUCTIONS
from synapse.mcp.workspace import configure_workspace

mcp = FastMCP("synapse", instructions=SERVER_INSTRUCTIONS)


def run(workspace_path: str | Path | None = None) -> None:
    """Run Synapse over stdio, allowing lazy initialization of new workspaces."""
    workspace_root = configure_workspace(workspace_path)
    if read_metadata(workspace_root) is not None:
        ensure_watch_daemon(workspace_root)
    from synapse.mcp import tools

    _ = tools
    mcp.run(transport="stdio")
