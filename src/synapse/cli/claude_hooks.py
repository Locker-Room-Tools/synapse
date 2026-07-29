"""Suggest-only Claude Code PreToolUse hook and its settings.json installer."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from synapse.core.workspace import (
    DEFAULT_DB_NAME,
    data_dir_path,
    detect_workspace_root,
)

HOOK_COMMAND = "synapse hook claude-pre-bash"
HOOK_TIMEOUT_SECONDS = 10
CLAUDE_SETTINGS_PATH = "~/.claude/settings.json"

_EXPLORATION_PATTERN = re.compile(r"(?:^|[|;&(]\s*)(?:command\s+)?(?:grep|rg|cat|find|tree)\b")

_REMINDER = (
    "This workspace is indexed by Synapse. For code navigation prefer the MCP tools over "
    "shell exploration: synapse_project_map (layout), synapse_search_symbols / "
    "synapse_get_definition (find symbols), synapse_find_references (usages), "
    "synapse_get_file_outline (file structure), and "
    "synapse_get_symbol_context(include_body=True) (implementation source). Shell search "
    "remains fine for exact text or content Synapse does not index."
)


@dataclass(frozen=True, slots=True)
class HookInstallResult:
    """Result of installing or removing the Claude Code hook."""

    path: Path
    status: str


def resolve_claude_settings_path() -> Path:
    """Return the global Claude Code settings file."""
    return Path(CLAUDE_SETTINGS_PATH).expanduser()


def _workspace_is_indexed(cwd: str) -> bool:
    workspace_root = detect_workspace_root(cwd)
    return (data_dir_path(workspace_root) / DEFAULT_DB_NAME).exists()


def run_claude_pre_bash(stdin: TextIO, stdout: TextIO) -> int:
    """Emit a non-blocking Synapse reminder for shell exploration commands.

    Never blocks the tool call and never fails: the hook only adds context, so any
    parsing or lookup problem degrades to silence.
    """
    try:
        payload = json.load(stdin)
        if payload.get("tool_name") != "Bash":
            return 0
        command = str(payload.get("tool_input", {}).get("command", ""))
        if not _EXPLORATION_PATTERN.search(command):
            return 0
        if not _workspace_is_indexed(str(payload.get("cwd", "."))):
            return 0
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": _REMINDER,
                }
            },
            stdout,
        )
    except Exception:  # noqa: BLE001 - a hook must never break the user's tool call
        return 0
    return 0


def _hook_entry() -> dict[str, object]:
    return {
        "type": "command",
        "command": HOOK_COMMAND,
        "timeout": HOOK_TIMEOUT_SECONDS,
    }


def _load_settings(path: Path) -> dict[str, object]:
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


def _has_managed_entry(settings: dict[str, object]) -> bool:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    groups = hooks.get("PreToolUse")
    if not isinstance(groups, list):
        return False
    for group in groups:
        if not isinstance(group, dict):
            continue
        for entry in group.get("hooks", []):
            if isinstance(entry, dict) and entry.get("command") == HOOK_COMMAND:
                return True
    return False


def install_claude_hook(*, dry_run: bool = False) -> HookInstallResult:
    """Register the suggest-only PreToolUse hook in global Claude Code settings."""
    path = resolve_claude_settings_path()
    existed = path.exists()
    settings = _load_settings(path)
    if _has_managed_entry(settings):
        return HookInstallResult(path=path, status="unchanged")
    if dry_run:
        return HookInstallResult(path=path, status="would-update" if existed else "would-create")

    hooks = settings.setdefault("hooks", {})
    assert isinstance(hooks, dict)
    groups = hooks.setdefault("PreToolUse", [])
    assert isinstance(groups, list)
    groups.append({"matcher": "Bash", "hooks": [_hook_entry()]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return HookInstallResult(path=path, status="updated" if existed else "created")


def remove_claude_hook(*, dry_run: bool = False) -> HookInstallResult:
    """Remove the managed hook entry, leaving all other settings intact."""
    path = resolve_claude_settings_path()
    if not path.exists():
        return HookInstallResult(path=path, status="absent")
    settings = _load_settings(path)
    if not _has_managed_entry(settings):
        return HookInstallResult(path=path, status="absent")
    if dry_run:
        return HookInstallResult(path=path, status="would-remove")

    hooks = settings["hooks"]
    assert isinstance(hooks, dict)
    groups = hooks["PreToolUse"]
    assert isinstance(groups, list)
    kept_groups: list[object] = []
    for group in groups:
        if not isinstance(group, dict):
            kept_groups.append(group)
            continue
        entries = [
            entry
            for entry in group.get("hooks", [])
            if not (isinstance(entry, dict) and entry.get("command") == HOOK_COMMAND)
        ]
        if entries:
            kept_groups.append({**group, "hooks": entries})
    if kept_groups:
        hooks["PreToolUse"] = kept_groups
    else:
        del hooks["PreToolUse"]
    if not hooks:
        del settings["hooks"]
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return HookInstallResult(path=path, status="removed")
