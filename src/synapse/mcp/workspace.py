"""Default workspace context for MCP tools."""

from pathlib import Path

from synapse.core.workspace import require_workspace_path

_workspace_root: Path | None = None


def configure_workspace(path: str | Path) -> Path:
    """Set the default workspace used by MCP tools when no path is supplied."""
    global _workspace_root
    _workspace_root = require_workspace_path(path)
    return _workspace_root


def current_workspace() -> Path:
    """Return the configured default workspace, falling back to the process cwd."""
    if _workspace_root is not None:
        return _workspace_root
    return require_workspace_path(Path.cwd())
