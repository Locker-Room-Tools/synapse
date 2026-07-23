"""Default workspace context for MCP tools."""

from pathlib import Path

from synapse.core.workspace import detect_workspace_root, require_workspace_path

_workspace_root: Path | None = None


def configure_workspace(path: str | Path | None = None) -> Path:
    """Set the default workspace used by MCP tools when no path is supplied."""
    global _workspace_root
    _workspace_root = (
        detect_workspace_root(Path.cwd()) if path is None else require_workspace_path(path)
    )
    return _workspace_root


def current_workspace() -> Path:
    """Return the configured default workspace, falling back to the process cwd."""
    if _workspace_root is not None:
        return _workspace_root
    return detect_workspace_root(Path.cwd())
