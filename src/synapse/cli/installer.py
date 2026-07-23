"""MCP client config install and uninstall helpers."""

import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from synapse.cli.adapters import (
    AgentAdapter,
    get_adapter,
    render_mcp_config,
    resolve_agent_user_path,
)
from synapse.cli.marker_blocks import append_marker_block, find_marker_block, splice_marker_block
from synapse.core.workspace import normalize_workspace_path

MANAGED_TOML_BEGIN = "# >>> SYNAPSE MCP (managed) >>>"
MANAGED_TOML_END = "# <<< SYNAPSE MCP (managed) <<<"
_PARTIAL_MARKERS_MESSAGE = (
    "MCP config contains partial Synapse managed markers; fix the file manually."
)
_CODEX_SYNAPSE_TABLE = re.compile(
    r"(?ms)^\[mcp_servers\.synapse\]\n.*?(?=^\[|\Z)",
)

JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class InstallResult:
    """Result of installing or uninstalling an MCP config entry."""

    path: Path
    action: str
    content_preview: str


def resolve_config_path(
    adapter: AgentAdapter,
    workspace_root: str | Path,
    scope: str | None = None,
) -> Path:
    """Resolve an adapter's MCP config path for project or user scope."""
    resolved_scope = scope or adapter.default_scope
    workspace = normalize_workspace_path(workspace_root)
    if resolved_scope == "project":
        if adapter.config.relative_path is None:
            msg = f"{adapter.display_name} does not support project-scope MCP config."
            raise ValueError(msg)
        return workspace / adapter.config.relative_path
    if resolved_scope == "user":
        if adapter.config.user_path is None:
            msg = f"{adapter.display_name} does not support user-scope MCP config."
            raise ValueError(msg)
        return resolve_agent_user_path(adapter.id, adapter.config.user_path)
    msg = f"Unsupported MCP config scope: {resolved_scope}"
    raise ValueError(msg)


def render_codex_toml_block(
    workspace_root: str | Path | None,
    python_executable: str | None = None,
) -> str:
    """Render the marker-managed Codex TOML server block."""
    workspace = None if workspace_root is None else normalize_workspace_path(workspace_root)
    content = render_mcp_config(
        workspace,
        agent_id="codex",
        python_executable=python_executable,
    ).strip()
    return f"{MANAGED_TOML_BEGIN}\n{content}\n{MANAGED_TOML_END}"


def install_mcp_server(
    agent_id: str,
    workspace_root: str | Path,
    *,
    scope: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    python_executable: str | None = None,
    portable: bool = False,
) -> InstallResult:
    """Install a workspace-pinned or portable Synapse MCP server entry."""
    adapter = get_adapter(agent_id)
    workspace = normalize_workspace_path(workspace_root)
    target = resolve_config_path(adapter, workspace, scope)
    if adapter.config.fmt == "json":
        return _install_json_config(
            adapter,
            target,
            workspace,
            force=force,
            dry_run=dry_run,
            python_executable=python_executable,
            portable=portable,
        )
    if adapter.config.fmt == "toml":
        return _install_toml_config(
            target,
            workspace,
            force=force,
            dry_run=dry_run,
            python_executable=python_executable,
            portable=portable,
        )
    msg = f"Unsupported MCP config format: {adapter.config.fmt}"
    raise ValueError(msg)


def uninstall_mcp_server(
    agent_id: str,
    workspace_root: str | Path,
    *,
    scope: str | None = None,
    dry_run: bool = False,
) -> InstallResult:
    """Remove a Synapse MCP server entry from a client config."""
    adapter = get_adapter(agent_id)
    workspace = normalize_workspace_path(workspace_root)
    target = resolve_config_path(adapter, workspace, scope)
    if adapter.config.fmt == "json":
        return _uninstall_json_config(adapter, target, dry_run=dry_run)
    if adapter.config.fmt == "toml":
        return _uninstall_toml_config(target, dry_run=dry_run)
    msg = f"Unsupported MCP config format: {adapter.config.fmt}"
    raise ValueError(msg)


def uninstall_global_mcp_server(
    agent_id: str,
    workspace_root: str | Path,
    *,
    dry_run: bool = False,
) -> InstallResult:
    """Remove the portable global MCP entry without touching pinned entries."""
    adapter = get_adapter(agent_id)
    target = resolve_config_path(adapter, workspace_root, "user")
    if adapter.config.fmt == "json":
        return _uninstall_json_config(
            adapter,
            target,
            dry_run=dry_run,
            expected_workspace=None,
        )
    if adapter.config.fmt == "toml":
        return _uninstall_toml_config(
            target,
            dry_run=dry_run,
            expected_block=render_codex_toml_block(None),
        )
    msg = f"Unsupported MCP config format: {adapter.config.fmt}"
    raise ValueError(msg)


def config_has_mcp_server(
    agent_id: str,
    workspace_root: str | Path,
    *,
    scope: str | None = None,
) -> bool:
    """Return whether the resolved client config contains a Synapse MCP entry."""
    adapter = get_adapter(agent_id)
    path = resolve_config_path(adapter, workspace_root, scope)
    if not path.exists():
        return False
    try:
        if adapter.config.fmt == "json":
            data = _read_json_file(path)
            server_parent = _lookup_mapping(data, adapter.config.json_key_path)
            return isinstance(server_parent.get("synapse"), dict)
        if adapter.config.fmt == "toml":
            text = path.read_text(encoding="utf-8")
            parsed = tomllib.loads(text)
            servers = parsed.get("mcp_servers")
            return isinstance(servers, dict) and isinstance(servers.get("synapse"), dict)
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return False
    return False


def _install_json_config(
    adapter: AgentAdapter,
    target: Path,
    workspace_root: Path,
    *,
    force: bool,
    dry_run: bool,
    python_executable: str | None,
    portable: bool,
) -> InstallResult:
    created = not target.exists()
    data = _read_json_file(target) if target.exists() else {}
    payload = cast(
        JsonObject,
        json.loads(
            render_mcp_config(
                None if portable else workspace_root,
                agent_id=adapter.id,
                python_executable=python_executable,
            )
        ),
    )
    if adapter.id == "opencode" and "$schema" not in data and "$schema" in payload:
        data["$schema"] = payload["$schema"]
    desired_parent = _lookup_mapping(payload, adapter.config.json_key_path)
    desired_entry = desired_parent["synapse"]
    parent = _ensure_mapping(data, adapter.config.json_key_path)
    current_entry = parent.get("synapse")
    if current_entry == desired_entry:
        content = _json_text(data)
        return InstallResult(target, "unchanged", content)
    if current_entry is not None and not force:
        msg = f"{target} already contains a different synapse MCP entry; use --force."
        raise FileExistsError(msg)
    parent["synapse"] = desired_entry
    content = _json_text(data)
    action = "created" if created else "updated"
    if dry_run:
        return InstallResult(target, _dry_run_action(action), content)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return InstallResult(target, action, content)


def _uninstall_json_config(
    adapter: AgentAdapter,
    target: Path,
    *,
    dry_run: bool,
    expected_workspace: Path | None | object = ...,
) -> InstallResult:
    if not target.exists():
        return InstallResult(target, "absent", "")
    data = _read_json_file(target)
    parent = _lookup_mapping(data, adapter.config.json_key_path)
    if "synapse" not in parent:
        return InstallResult(target, "absent", _json_text(data))
    if expected_workspace is not ...:
        expected = cast(Path | None, expected_workspace)
        expected_payload = cast(
            JsonObject,
            json.loads(render_mcp_config(expected, agent_id=adapter.id)),
        )
        expected_parent = _lookup_mapping(expected_payload, adapter.config.json_key_path)
        if parent["synapse"] != expected_parent["synapse"]:
            return InstallResult(target, "unmanaged", _json_text(data))
    del parent["synapse"]
    _drop_empty_path(data, adapter.config.json_key_path)
    if _json_effectively_empty(data, adapter):
        if dry_run:
            return InstallResult(target, "would-remove", "")
        target.unlink()
        return InstallResult(target, "removed", "")
    content = _json_text(data)
    if dry_run:
        return InstallResult(target, "would-remove", content)
    target.write_text(content, encoding="utf-8")
    return InstallResult(target, "removed", content)


def _install_toml_config(
    target: Path,
    workspace_root: Path,
    *,
    force: bool,
    dry_run: bool,
    python_executable: str | None,
    portable: bool,
) -> InstallResult:
    created = not target.exists()
    block = render_codex_toml_block(
        None if portable else workspace_root,
        python_executable,
    )
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    next_text, changed = _upsert_managed_toml_block(existing, block, force=force)
    if not changed:
        return InstallResult(target, "unchanged", next_text)
    action = "created" if created else "updated"
    if dry_run:
        return InstallResult(target, _dry_run_action(action), next_text)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(next_text, encoding="utf-8")
    return InstallResult(target, action, next_text)


def _uninstall_toml_config(
    target: Path,
    *,
    dry_run: bool,
    expected_block: str | None = None,
) -> InstallResult:
    if not target.exists():
        return InstallResult(target, "absent", "")
    existing = target.read_text(encoding="utf-8")
    if expected_block is not None:
        span = find_marker_block(
            existing,
            MANAGED_TOML_BEGIN,
            MANAGED_TOML_END,
            partial_message=_PARTIAL_MARKERS_MESSAGE,
        )
        if span is None:
            return InstallResult(target, "absent", existing)
        start, block_end = span
        if existing[start:block_end].strip() != expected_block:
            return InstallResult(target, "unmanaged", existing)
    next_text, removed = _remove_managed_toml_block(existing)
    if not removed:
        return InstallResult(target, "absent", existing)
    if dry_run:
        return InstallResult(target, "would-remove", next_text)
    if next_text.strip():
        target.write_text(next_text, encoding="utf-8")
    else:
        target.unlink()
    return InstallResult(target, "removed", next_text)


def _upsert_managed_toml_block(
    existing: str,
    block: str,
    *,
    force: bool,
) -> tuple[str, bool]:
    span = find_marker_block(
        existing, MANAGED_TOML_BEGIN, MANAGED_TOML_END, partial_message=_PARTIAL_MARKERS_MESSAGE
    )
    if span is not None:
        start, block_end = span
        if existing[start:block_end].strip() == block:
            return existing, False
        if not force:
            msg = "Synapse MCP block already exists; use --force to replace it."
            raise FileExistsError(msg)
        return splice_marker_block(existing, span, block), True

    if _has_codex_synapse_table(existing):
        if not force:
            msg = "Codex config already contains synapse MCP config; use --force to manage it."
            raise FileExistsError(msg)
        existing = _strip_codex_synapse_table(existing)

    return append_marker_block(existing, block), True


def _remove_managed_toml_block(existing: str) -> tuple[str, bool]:
    span = find_marker_block(
        existing, MANAGED_TOML_BEGIN, MANAGED_TOML_END, partial_message=_PARTIAL_MARKERS_MESSAGE
    )
    if span is None:
        return existing, False
    return splice_marker_block(existing, span), True


def _has_codex_synapse_table(text: str) -> bool:
    if not text.strip():
        return False
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        msg = "Codex config contains invalid TOML."
        raise ValueError(msg) from exc
    servers = parsed.get("mcp_servers")
    return isinstance(servers, dict) and isinstance(servers.get("synapse"), dict)


def _strip_codex_synapse_table(text: str) -> str:
    next_text, replacements = _CODEX_SYNAPSE_TABLE.subn("", text)
    if replacements == 0:
        msg = "Existing Codex synapse config is not a simple table; remove it manually."
        raise ValueError(msg)
    return next_text.strip() + "\n" if next_text.strip() else ""


def _read_json_file(path: Path) -> JsonObject:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        msg = f"{path} must contain a JSON object."
        raise ValueError(msg)
    return cast(JsonObject, data)


def _json_text(data: JsonObject) -> str:
    return json.dumps(data, indent=2) + "\n"


def _ensure_mapping(data: JsonObject, key_path: tuple[str, ...]) -> JsonObject:
    current = data
    for key in key_path:
        child = current.get(key)
        if child is None:
            child = {}
            current[key] = child
        if not isinstance(child, dict):
            msg = f"Config key {key} must be an object."
            raise ValueError(msg)
        current = cast(JsonObject, child)
    return current


def _lookup_mapping(data: JsonObject, key_path: tuple[str, ...]) -> JsonObject:
    current = data
    for key in key_path:
        child = current.get(key)
        if child is None:
            return {}
        if not isinstance(child, dict):
            msg = f"Config key {key} must be an object."
            raise ValueError(msg)
        current = cast(JsonObject, child)
    return current


def _drop_empty_path(data: JsonObject, key_path: tuple[str, ...]) -> None:
    if not key_path:
        return
    stack: list[tuple[JsonObject, str]] = []
    current = data
    for key in key_path:
        child = current.get(key)
        if not isinstance(child, dict):
            return
        stack.append((current, key))
        current = cast(JsonObject, child)
    while stack and not current:
        parent, key = stack.pop()
        del parent[key]
        current = parent


def _json_effectively_empty(data: JsonObject, adapter: AgentAdapter) -> bool:
    if not data:
        return True
    return adapter.id == "opencode" and set(data) == {"$schema"}


def _dry_run_action(action: str) -> str:
    if action == "created":
        return "would-create"
    if action == "updated":
        return "would-update"
    return f"would-{action}"


def standalone_mcp_config(
    agent_id: str,
    workspace_root: str | Path,
    *,
    python_executable: str | None = None,
) -> str:
    """Render a standalone MCP config for printing or explicit output files."""
    command = python_executable or sys.executable
    return render_mcp_config(workspace_root, agent_id=agent_id, python_executable=command)
