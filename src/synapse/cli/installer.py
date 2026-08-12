"""MCP client config install and uninstall helpers."""

import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse.cli.adapters import (
    AgentAdapter,
    ConfigFormat,
    McpTarget,
    build_server_entry,
    get_adapter,
    render_mcp_config,
    resolve_user_path,
)
from synapse.cli.adapters.model import JsonObject
from synapse.cli.config_codecs import (
    SERVER_NAME,
    apply_document_defaults,
    delete_entry,
    document_is_empty,
    dumps,
    loads,
    read_entry,
    render_toml_entry,
    write_entry,
)
from synapse.cli.marker_blocks import append_marker_block, find_marker_block, splice_marker_block
from synapse.core.workspace import normalize_workspace_path

MANAGED_TOML_BEGIN = "# >>> SYNAPSE MCP (managed) >>>"
MANAGED_TOML_END = "# <<< SYNAPSE MCP (managed) <<<"
_PARTIAL_MARKERS_MESSAGE = (
    "MCP config contains partial Synapse managed markers; fix the file manually."
)


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
        if adapter.mcp.project is None:
            msg = (
                f"{adapter.display_name} does not support project-scope MCP config; "
                f"use 'synapse install {adapter.id}' for its global configuration."
            )
            raise ValueError(msg)
        return workspace / adapter.mcp.project
    if resolved_scope == "user":
        if adapter.mcp.user is None:
            msg = (
                f"{adapter.display_name} does not support user-scope MCP config; "
                f"use 'synapse setup {adapter.id} --path .' for its project configuration."
            )
            raise ValueError(msg)
        return resolve_user_path(adapter.mcp.user)
    msg = f"Unsupported MCP config scope: {resolved_scope}"
    raise ValueError(msg)


def render_managed_toml_block(
    adapter: AgentAdapter,
    workspace_root: str | Path | None,
    python_executable: str | None = None,
) -> str:
    """Render the marker-managed TOML server block for an adapter."""
    workspace = None if workspace_root is None else normalize_workspace_path(workspace_root)
    entry = build_server_entry(adapter.mcp, workspace, python_executable)
    content = render_toml_entry(adapter.mcp, entry).strip()
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
    resolved_scope = scope or adapter.default_scope
    if resolved_scope == "user" and adapter.mcp.user_requires_existing and not target.exists():
        msg = (
            f"{target} does not exist. {adapter.display_name} requires an existing "
            "configuration file with its mandatory top-level keys; start the agent once "
            f"to create it, or use 'synapse setup {adapter.id} --path .' for project scope."
        )
        raise ValueError(msg)
    if adapter.mcp.fmt is ConfigFormat.TOML:
        return _install_toml_config(
            adapter,
            target,
            workspace,
            force=force,
            dry_run=dry_run,
            python_executable=python_executable,
            portable=portable,
        )
    return _install_structured_config(
        adapter,
        target,
        workspace,
        force=force,
        dry_run=dry_run,
        python_executable=python_executable,
        portable=portable,
    )


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
    if adapter.mcp.fmt is ConfigFormat.TOML:
        return _uninstall_toml_config(target, dry_run=dry_run)
    resolved_scope = scope or adapter.default_scope
    return _uninstall_structured_config(
        adapter,
        target,
        dry_run=dry_run,
        allow_delete=not (resolved_scope == "user" and adapter.mcp.user_requires_existing),
    )


def uninstall_global_mcp_server(
    agent_id: str,
    workspace_root: str | Path,
    *,
    dry_run: bool = False,
) -> InstallResult:
    """Remove the portable global MCP entry without touching pinned entries."""
    adapter = get_adapter(agent_id)
    target = resolve_config_path(adapter, workspace_root, "user")
    if adapter.mcp.fmt is ConfigFormat.TOML:
        return _uninstall_toml_config(
            target,
            dry_run=dry_run,
            expected_block=render_managed_toml_block(adapter, None),
        )
    return _uninstall_structured_config(
        adapter,
        target,
        dry_run=dry_run,
        expected_workspace=None,
        allow_delete=not adapter.mcp.user_requires_existing,
    )


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
        text = path.read_text(encoding="utf-8")
        if adapter.mcp.fmt is ConfigFormat.TOML:
            return _toml_has_entry(text, adapter.mcp)
        data = loads(adapter.mcp.fmt, text, str(path))
        return read_entry(data, adapter.mcp) is not None
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return False


def _install_structured_config(
    adapter: AgentAdapter,
    target: Path,
    workspace_root: Path,
    *,
    force: bool,
    dry_run: bool,
    python_executable: str | None,
    portable: bool,
) -> InstallResult:
    mcp = adapter.mcp
    exists = target.exists()
    data = loads(mcp.fmt, target.read_text(encoding="utf-8"), str(target)) if exists else {}
    desired = build_server_entry(
        mcp,
        None if portable else workspace_root,
        python_executable,
    )
    if not exists:
        apply_document_defaults(data, mcp)
    current = read_entry(data, mcp)
    if current == desired:
        return InstallResult(target, "unchanged", dumps(mcp.fmt, data))
    if current is not None and not force:
        msg = f"{target} already contains a different {SERVER_NAME} MCP entry; use --force."
        raise FileExistsError(msg)
    write_entry(data, mcp, desired)
    content = dumps(mcp.fmt, data)
    action = "updated" if exists else "created"
    if dry_run:
        return InstallResult(target, _dry_run_action(action), content)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return InstallResult(target, action, content)


def _uninstall_structured_config(
    adapter: AgentAdapter,
    target: Path,
    *,
    dry_run: bool,
    expected_workspace: Path | None | object = ...,
    allow_delete: bool = True,
) -> InstallResult:
    mcp = adapter.mcp
    if not target.exists():
        return InstallResult(target, "absent", "")
    data = loads(mcp.fmt, target.read_text(encoding="utf-8"), str(target))
    current = read_entry(data, mcp)
    if current is None:
        return InstallResult(target, "absent", dumps(mcp.fmt, data))
    if expected_workspace is not ...:
        expected = build_server_entry(mcp, _as_path(expected_workspace))
        if current != expected:
            return InstallResult(target, "unmanaged", dumps(mcp.fmt, data))
    delete_entry(data, mcp)
    if allow_delete and document_is_empty(data, mcp):
        if dry_run:
            return InstallResult(target, "would-remove", "")
        target.unlink()
        return InstallResult(target, "removed", "")
    content = dumps(mcp.fmt, data)
    if dry_run:
        return InstallResult(target, "would-remove", content)
    target.write_text(content, encoding="utf-8")
    return InstallResult(target, "removed", content)


def _as_path(value: Path | None | object) -> Path | None:
    return value if isinstance(value, Path) else None


def _install_toml_config(
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
    block = render_managed_toml_block(
        adapter,
        None if portable else workspace_root,
        python_executable,
    )
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    next_text, changed = _upsert_managed_toml_block(existing, block, adapter.mcp, force=force)
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
    mcp: McpTarget,
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

    if _toml_has_entry(existing, mcp):
        if not force:
            msg = (
                f"Config already contains an unmanaged {SERVER_NAME} MCP table; "
                "use --force to manage it."
            )
            raise FileExistsError(msg)
        existing = _strip_toml_entry(existing, mcp)

    return append_marker_block(existing, block), True


def _remove_managed_toml_block(existing: str) -> tuple[str, bool]:
    span = find_marker_block(
        existing, MANAGED_TOML_BEGIN, MANAGED_TOML_END, partial_message=_PARTIAL_MARKERS_MESSAGE
    )
    if span is None:
        return existing, False
    return splice_marker_block(existing, span), True


def _toml_entry_pattern(mcp: McpTarget) -> re.Pattern[str]:
    header = re.escape(".".join((*mcp.key_path, SERVER_NAME)))
    return re.compile(rf"(?ms)^\[{header}\]\n.*?(?=^\[|\Z)")


def _toml_has_entry(text: str, mcp: McpTarget) -> bool:
    if not text.strip():
        return False
    try:
        parsed: dict[str, Any] = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        msg = "MCP config contains invalid TOML."
        raise ValueError(msg) from exc
    current: Any = parsed
    for key in mcp.key_path:
        if not isinstance(current, dict):
            return False
        current = current.get(key)
    return isinstance(current, dict) and isinstance(current.get(SERVER_NAME), dict)


def _strip_toml_entry(text: str, mcp: McpTarget) -> str:
    next_text, replacements = _toml_entry_pattern(mcp).subn("", text)
    if replacements == 0:
        msg = f"Existing {SERVER_NAME} config is not a simple table; remove it manually."
        raise ValueError(msg)
    return next_text.strip() + "\n" if next_text.strip() else ""


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


__all__ = [
    "MANAGED_TOML_BEGIN",
    "MANAGED_TOML_END",
    "InstallResult",
    "JsonObject",
    "config_has_mcp_server",
    "install_mcp_server",
    "render_managed_toml_block",
    "resolve_config_path",
    "standalone_mcp_config",
    "uninstall_global_mcp_server",
    "uninstall_mcp_server",
]
