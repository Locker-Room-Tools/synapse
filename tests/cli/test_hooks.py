"""Tests for the suggest-only pre-shell hooks and their installers."""

import io
import json
from pathlib import Path

import pytest

from synapse.cli.adapters import ADAPTERS, get_adapter
from synapse.cli.hooks import (
    codec_choices,
    hook_command,
    install_hook,
    remove_hook,
    resolve_hook_settings_path,
    run_hook,
)
from synapse.core.workspace import DEFAULT_DB_NAME, data_dir_path

from .conftest import adapter_env_vars

HOOK_AGENTS = sorted(agent for agent, adapter in ADAPTERS.items() if adapter.hook is not None)

# codec -> the tool name that agent uses for its shell tool.
SHELL_TOOLS = {
    "claude-pre-bash": "Bash",
    "qwen-pre-bash": "run_shell_command",
    "crush-pre-bash": "bash",
}


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for name in adapter_env_vars():
        monkeypatch.delenv(name, raising=False)
    return home


def _indexed_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db_file = data_dir_path(workspace) / DEFAULT_DB_NAME
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_file.touch()
    return workspace


def _run(codec: str, payload: object) -> str:
    stdout = io.StringIO()
    assert run_hook(codec, io.StringIO(json.dumps(payload)), stdout) == 0
    return stdout.getvalue()


def _explore(codec: str, workspace: Path, command: str = "grep -rn TODO src") -> str:
    return _run(
        codec,
        {
            "tool_name": SHELL_TOOLS[codec],
            "tool_input": {"command": command},
            "cwd": str(workspace),
        },
    )


def test_every_codec_has_a_shell_tool_fixture() -> None:
    """A new codec cannot ship without a row in this file."""
    assert set(SHELL_TOOLS) == set(codec_choices())


def test_claude_hook_output_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Claude Code payload keeps its exact pre-refactor shape."""
    workspace = _indexed_workspace(tmp_path, monkeypatch)
    payload = json.loads(_explore("claude-pre-bash", workspace))

    hook_output = payload["hookSpecificOutput"]
    assert set(payload) == {"hookSpecificOutput"}
    assert hook_output["hookEventName"] == "PreToolUse"
    assert "synapse_orient" in hook_output["additionalContext"]
    assert "synapse_inspect" in hook_output["additionalContext"]
    assert "permissionDecision" not in hook_output


def test_qwen_hook_allows_explicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Qwen requires a permission decision, so the nudge must allow the call."""
    workspace = _indexed_workspace(tmp_path, monkeypatch)
    hook_output = json.loads(_explore("qwen-pre-bash", workspace))["hookSpecificOutput"]

    assert hook_output["permissionDecision"] == "allow"
    assert hook_output["permissionDecisionReason"]
    assert "synapse_orient" in hook_output["additionalContext"]


def test_crush_hook_expresses_no_opinion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Crush treats an omitted decision as 'no opinion', keeping the normal prompt."""
    workspace = _indexed_workspace(tmp_path, monkeypatch)
    payload = json.loads(_explore("crush-pre-bash", workspace))

    assert payload["version"] == 1
    assert "synapse_orient" in payload["context"]
    assert "decision" not in payload


@pytest.mark.parametrize("codec", sorted(SHELL_TOOLS))
def test_hook_suggests_synapse_for_exploration_in_indexed_workspace(
    codec: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """grep in an indexed workspace produces a non-blocking reminder payload."""
    workspace = _indexed_workspace(tmp_path, monkeypatch)
    assert "synapse_orient" in _explore(codec, workspace)


@pytest.mark.parametrize("codec", sorted(SHELL_TOOLS))
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
    codec: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    workspace = _indexed_workspace(tmp_path, monkeypatch)
    assert _explore(codec, workspace, command) == ""


@pytest.mark.parametrize("codec", sorted(SHELL_TOOLS))
def test_hook_stays_silent_for_another_agents_tool_name(
    codec: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each codec answers only for its own agent's shell tool."""
    workspace = _indexed_workspace(tmp_path, monkeypatch)
    for other, tool_name in SHELL_TOOLS.items():
        if other == codec:
            continue
        payload = {
            "tool_name": tool_name,
            "tool_input": {"command": "grep -rn TODO src"},
            "cwd": str(workspace),
        }
        assert _run(codec, payload) == ""


@pytest.mark.parametrize("codec", sorted(SHELL_TOOLS))
def test_hook_stays_silent_outside_indexed_workspaces(
    codec: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert _explore(codec, workspace, "cat README.md") == ""


@pytest.mark.parametrize("codec", sorted(SHELL_TOOLS))
def test_hook_swallows_malformed_input(codec: str) -> None:
    stdout = io.StringIO()
    assert run_hook(codec, io.StringIO("not json"), stdout) == 0
    assert stdout.getvalue() == ""


def test_unknown_codec_stays_silent() -> None:
    stdout = io.StringIO()
    assert run_hook("nope-pre-bash", io.StringIO("{}"), stdout) == 0
    assert stdout.getvalue() == ""


@pytest.mark.parametrize("agent", HOOK_AGENTS)
def test_install_and_remove_hook_round_trip(agent: str, isolated_home: Path) -> None:
    """Install merges the hook, is idempotent, and remove leaves other settings alone."""
    original = {"model": "opus", "hooks": {"PostToolUse": []}}
    settings_path = resolve_hook_settings_path(agent)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(original), encoding="utf-8")

    assert install_hook(agent).status == "updated"
    assert install_hook(agent).status == "unchanged"

    target = get_adapter(agent).hook
    assert target is not None
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["model"] == "opus"
    groups = settings["hooks"]["PreToolUse"]
    assert groups[0]["matcher"] == target.matcher
    assert hook_command(target) in json.dumps(groups[0])

    assert remove_hook(agent).status == "removed"
    assert remove_hook(agent).status == "absent"
    assert json.loads(settings_path.read_text(encoding="utf-8")) == original


@pytest.mark.parametrize("agent", HOOK_AGENTS)
def test_install_hook_creates_settings_file(agent: str, isolated_home: Path) -> None:
    result = install_hook(agent)
    target = get_adapter(agent).hook
    assert target is not None
    assert result.status == "created"
    settings = json.loads(result.path.read_text(encoding="utf-8"))
    assert hook_command(target) in json.dumps(settings["hooks"]["PreToolUse"])


@pytest.mark.parametrize("agent", HOOK_AGENTS)
def test_install_hook_dry_run_writes_nothing(agent: str, isolated_home: Path) -> None:
    result = install_hook(agent, dry_run=True)
    assert result.status == "would-create"
    assert not result.path.exists()


@pytest.mark.parametrize("agent", HOOK_AGENTS)
def test_remove_hook_deletes_a_settings_file_it_emptied(agent: str, isolated_home: Path) -> None:
    """A settings file that held only the hook is removed, not left as an empty stub."""
    install_hook(agent)
    path = resolve_hook_settings_path(agent)

    assert remove_hook(agent).status == "removed"
    assert not path.exists()


def test_crush_hook_shares_the_file_with_its_mcp_entry(isolated_home: Path) -> None:
    """Crush stores hooks and MCP servers in one crush.json; neither clobbers the other."""
    from synapse.cli.installer import install_mcp_server

    install_mcp_server("crush", Path.cwd(), scope="user", portable=True)
    install_hook("crush")

    settings = json.loads(resolve_hook_settings_path("crush").read_text(encoding="utf-8"))
    assert settings["mcp"]["synapse"]["type"] == "stdio"
    entry = settings["hooks"]["PreToolUse"][0]
    assert entry["command"] == "synapse hook crush-pre-bash"
    assert entry["matcher"] == "^bash$"
    assert "hooks" not in entry


def test_hook_is_unsupported_for_agents_without_one() -> None:
    with pytest.raises(ValueError, match="does not support a Synapse hook"):
        install_hook("cursor")
