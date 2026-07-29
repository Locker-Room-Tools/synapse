"""Tests for the suggest-only Claude Code hook and its installer."""

import io
import json
from pathlib import Path

import pytest

from synapse.cli.claude_hooks import (
    HOOK_COMMAND,
    install_claude_hook,
    remove_claude_hook,
    resolve_claude_settings_path,
    run_claude_pre_bash,
)
from synapse.core.workspace import DEFAULT_DB_NAME, data_dir_path


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def _indexed_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db_file = data_dir_path(workspace) / DEFAULT_DB_NAME
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_file.touch()
    return workspace


def _run(payload: object) -> str:
    stdout = io.StringIO()
    assert run_claude_pre_bash(io.StringIO(json.dumps(payload)), stdout) == 0
    return stdout.getvalue()


def test_hook_suggests_synapse_for_exploration_in_indexed_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """grep in an indexed workspace produces a non-blocking additionalContext payload."""
    workspace = _indexed_workspace(tmp_path, monkeypatch)
    output = _run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "grep -rn TODO src"},
            "cwd": str(workspace),
        }
    )

    payload = json.loads(output)
    hook_output = payload["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "PreToolUse"
    assert "synapse_search_symbols" in hook_output["additionalContext"]
    assert "permissionDecision" not in hook_output


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "dotnet test",
        "concatenate.sh",
        "echo find-me",
    ],
)
def test_hook_stays_silent_for_non_exploration_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    workspace = _indexed_workspace(tmp_path, monkeypatch)
    output = _run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(workspace)})
    assert output == ""


def test_hook_stays_silent_outside_indexed_workspaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = _run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "cat README.md"},
            "cwd": str(workspace),
        }
    )
    assert output == ""


def test_hook_swallows_malformed_input(tmp_path: Path) -> None:
    stdout = io.StringIO()
    assert run_claude_pre_bash(io.StringIO("not json"), stdout) == 0
    assert stdout.getvalue() == ""


def test_install_and_remove_hook_round_trip(isolated_home: Path) -> None:
    """Install merges the hook, is idempotent, and remove leaves other settings alone."""
    settings_path = resolve_claude_settings_path()
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"model": "opus", "hooks": {"PostToolUse": []}}),
        encoding="utf-8",
    )

    assert install_claude_hook().status == "updated"
    assert install_claude_hook().status == "unchanged"

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["model"] == "opus"
    groups = settings["hooks"]["PreToolUse"]
    assert groups[0]["matcher"] == "Bash"
    assert groups[0]["hooks"][0]["command"] == HOOK_COMMAND

    assert remove_claude_hook().status == "removed"
    assert remove_claude_hook().status == "absent"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings == {"model": "opus", "hooks": {"PostToolUse": []}}


def test_install_hook_creates_settings_file(isolated_home: Path) -> None:
    result = install_claude_hook()
    assert result.status == "created"
    settings = json.loads(result.path.read_text(encoding="utf-8"))
    assert settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == HOOK_COMMAND


def test_install_hook_dry_run_writes_nothing(isolated_home: Path) -> None:
    result = install_claude_hook(dry_run=True)
    assert result.status == "would-create"
    assert not result.path.exists()
