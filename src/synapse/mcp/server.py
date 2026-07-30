"""FastMCP server skeleton for Synapse."""

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from synapse.core.watch.daemon import ensure_watch_daemon
from synapse.core.workspace import read_metadata
from synapse.mcp.instructions import SERVER_INSTRUCTIONS
from synapse.mcp.profiles import ToolProfile, specs_for_profile
from synapse.mcp.workspace import configure_workspace

mcp = FastMCP("synapse", instructions=SERVER_INSTRUCTIONS)

_registered: dict[int, set[str]] = {}


def register_tools(server: FastMCP, profile: ToolProfile) -> None:
    """Register the profile's tools on a server, skipping already-registered names."""
    names = _registered.setdefault(id(server), set())
    for spec in specs_for_profile(profile):
        if spec.func.__name__ not in names:
            server.tool()(spec.func)
            names.add(spec.func.__name__)


def run(
    workspace_path: str | Path | None = None,
    profile: ToolProfile = ToolProfile.DEFAULT,
) -> None:
    """Run Synapse over stdio, allowing lazy initialization of new workspaces."""
    workspace_root = configure_workspace(workspace_path)
    if read_metadata(workspace_root) is not None:
        ensure_watch_daemon(workspace_root)
    register_tools(mcp, profile)
    mcp.run(transport="stdio")
