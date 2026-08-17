"""Idempotent, surgical registration of the Synapse hook in agent settings."""

import json
from dataclasses import dataclass
from pathlib import Path

from synapse.cli.adapters.model import HookShape, HookTarget, JsonObject
from synapse.cli.adapters.paths import resolve_user_path
from synapse.cli.adapters.registry import get_adapter

HOOK_COMMAND_TEMPLATE = "synapse hook {codec}"


@dataclass(frozen=True, slots=True)
class HookInstallResult:
    """Result of installing or removing an agent hook."""

    path: Path
    status: str


def hook_command(target: HookTarget) -> str:
    """Return the managed command string that identifies this hook."""
    return HOOK_COMMAND_TEMPLATE.format(codec=target.codec)


def resolve_hook_settings_path(agent_id: str) -> Path:
    """Return the settings file holding an adapter's hook registration."""
    return resolve_user_path(_require_hook(agent_id).settings)


def _require_hook(agent_id: str) -> HookTarget:
    adapter = get_adapter(agent_id)
    if adapter.hook is None:
        msg = f"{adapter.display_name} does not support a Synapse hook."
        raise ValueError(msg)
    return adapter.hook


def _load_settings(path: Path) -> JsonObject:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    settings = json.loads(text)
    if not isinstance(settings, dict):
        msg = f"{path} must contain a JSON object."
        raise ValueError(msg)
    return settings


def _groups(settings: JsonObject, target: HookTarget) -> list[object]:
    node: object = settings
    for key in target.key_path:
        if not isinstance(node, dict):
            return []
        node = node.get(key)
    return node if isinstance(node, list) else []


def _entry(target: HookTarget) -> JsonObject:
    command = hook_command(target)
    if target.shape is HookShape.FLAT:
        return {
            "name": "synapse-navigation-reminder",
            "matcher": target.matcher,
            "command": command,
            "timeout": target.timeout,
        }
    return {
        "matcher": target.matcher,
        "hooks": [{"type": "command", "command": command, "timeout": target.timeout}],
    }


def _group_owns_command(group: object, target: HookTarget) -> bool:
    if not isinstance(group, dict):
        return False
    command = hook_command(target)
    if target.shape is HookShape.FLAT:
        return group.get("command") == command
    return any(
        isinstance(entry, dict) and entry.get("command") == command
        for entry in group.get("hooks", [])
    )


def _has_managed_entry(settings: JsonObject, target: HookTarget) -> bool:
    return any(_group_owns_command(group, target) for group in _groups(settings, target))


def _prune_managed(group: object, target: HookTarget) -> object | None:
    """Return the group with the managed entry stripped, or None if it is empty."""
    if not isinstance(group, dict):
        return group
    if target.shape is HookShape.FLAT:
        return None if _group_owns_command(group, target) else group
    command = hook_command(target)
    entries = [
        entry
        for entry in group.get("hooks", [])
        if not (isinstance(entry, dict) and entry.get("command") == command)
    ]
    return {**group, "hooks": entries} if entries else None


def _write(path: Path, settings: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def _write_or_delete(path: Path, settings: JsonObject) -> None:
    """Persist settings, deleting a file the removal emptied.

    Several agents keep hooks in the same file as their MCP config, so a full
    uninstall must not leave an empty stub behind.
    """
    if settings:
        _write(path, settings)
    else:
        path.unlink(missing_ok=True)


def install_hook(agent_id: str, *, dry_run: bool = False) -> HookInstallResult:
    """Register the suggest-only pre-shell hook in an agent's global settings."""
    target = _require_hook(agent_id)
    path = resolve_hook_settings_path(agent_id)
    existed = path.exists()
    settings = _load_settings(path)
    if _has_managed_entry(settings, target):
        return HookInstallResult(path=path, status="unchanged")
    if dry_run:
        return HookInstallResult(path=path, status="would-update" if existed else "would-create")

    node = settings
    for key in target.key_path[:-1]:
        child = node.setdefault(key, {})
        if not isinstance(child, dict):
            msg = f"{path} has a non-object at {'.'.join(target.key_path)}."
            raise ValueError(msg)
        node = child
    groups = node.setdefault(target.key_path[-1], [])
    if not isinstance(groups, list):
        msg = f"{path} has a non-list at {'.'.join(target.key_path)}."
        raise ValueError(msg)
    groups.append(_entry(target))
    _write(path, settings)
    return HookInstallResult(path=path, status="updated" if existed else "created")


def remove_hook(agent_id: str, *, dry_run: bool = False) -> HookInstallResult:
    """Remove the managed hook entry, leaving all other settings intact."""
    target = _require_hook(agent_id)
    path = resolve_hook_settings_path(agent_id)
    if not path.exists():
        return HookInstallResult(path=path, status="absent")
    settings = _load_settings(path)
    if not _has_managed_entry(settings, target):
        return HookInstallResult(path=path, status="absent")
    if dry_run:
        return HookInstallResult(path=path, status="would-remove")

    parents: list[JsonObject] = [settings]
    for key in target.key_path[:-1]:
        parents.append(parents[-1][key])
    owner = parents[-1]

    kept = [
        pruned
        for pruned in (_prune_managed(group, target) for group in _groups(settings, target))
        if pruned is not None
    ]
    if kept:
        owner[target.key_path[-1]] = kept
    else:
        del owner[target.key_path[-1]]
        for key, parent in zip(reversed(target.key_path[:-1]), reversed(parents[:-1]), strict=True):
            if parent[key]:
                break
            del parent[key]
    _write_or_delete(path, settings)
    return HookInstallResult(path=path, status="removed")
