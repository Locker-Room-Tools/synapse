"""Rendering of the Synapse stdio MCP entry from declarative adapter metadata."""

import sys
from pathlib import Path

from synapse.cli.adapters.model import (
    ConfigFormat,
    ContainerShape,
    JsonObject,
    McpTarget,
    PayloadStyle,
)
from synapse.cli.adapters.registry import get_adapter
from synapse.cli.config_codecs import (
    SERVER_NAME,
    apply_document_defaults,
    dumps,
    render_toml_entry,
    write_entry,
)
from synapse.core.workspace import normalize_workspace_path


def server_command(
    workspace_path: str | Path | None,
    python_executable: str | None = None,
) -> tuple[str, list[str]]:
    """Return the stdio command and args for a portable or pinned server."""
    if workspace_path is None:
        return "synapse", ["serve"]
    workspace_root = normalize_workspace_path(workspace_path)
    command = python_executable or sys.executable
    return command, ["-m", "synapse", "serve", "--workspace", str(workspace_root)]


def build_server_entry(
    target: McpTarget,
    workspace_path: str | Path | None,
    python_executable: str | None = None,
) -> JsonObject:
    """Build the agent-specific stdio server entry."""
    command, args = server_command(workspace_path, python_executable)
    entry: JsonObject = {}
    if target.shape is ContainerShape.LIST and target.name_field:
        entry[target.name_field] = SERVER_NAME
    for key, value in target.extra_fields:
        entry[key] = list(value) if isinstance(value, tuple) else value
    if target.payload_style is PayloadStyle.COMMAND_LIST:
        entry["command"] = [command, *args]
    else:
        entry["command"] = command
        entry["args"] = args
    return entry


def render_mcp_config(
    workspace_path: str | Path | None,
    *,
    agent_id: str,
    python_executable: str | None = None,
) -> str:
    """Render a standalone MCP config document for one adapter."""
    target = get_adapter(agent_id).mcp
    entry = build_server_entry(target, workspace_path, python_executable)
    if target.fmt is ConfigFormat.TOML:
        return render_toml_entry(target, entry)
    document: JsonObject = {}
    apply_document_defaults(document, target)
    write_entry(document, target, entry)
    return dumps(target.fmt, document)
