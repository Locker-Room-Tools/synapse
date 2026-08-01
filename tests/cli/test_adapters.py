"""Tests for agent adapter config and instruction helpers."""

import json
import tomllib
from pathlib import Path

import pytest

from synapse.cli.adapters import (
    ADAPTERS,
    BEGIN_MARKER,
    END_MARKER,
    install_instruction_snippet,
    project_snippet,
    remove_instruction_snippet,
    render_mcp_config,
)


def test_render_mcp_config_pins_workspace_for_mcp_servers_agents(tmp_path: Path) -> None:
    """Claude Code uses the workspace-pinned mcpServers config shape."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    config = json.loads(
        render_mcp_config(
            workspace_root,
            agent_id="claude-code",
            python_executable="/python-for-test",
        )
    )

    server = config["mcpServers"]["synapse"]
    assert server["command"] == "/python-for-test"
    assert server["args"] == [
        "-m",
        "synapse",
        "serve",
        "--workspace",
        str(workspace_root),
    ]


def test_render_mcp_config_uses_codex_toml_shape(tmp_path: Path) -> None:
    """Codex expects TOML under mcp_servers, not JSON mcpServers."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    config = tomllib.loads(
        render_mcp_config(
            workspace_root,
            agent_id="codex",
            python_executable="/python-for-test",
        )
    )

    server = config["mcp_servers"]["synapse"]
    assert server["command"] == "/python-for-test"
    assert server["args"] == [
        "-m",
        "synapse",
        "serve",
        "--workspace",
        str(workspace_root),
    ]


def test_render_mcp_config_uses_opencode_local_shape(tmp_path: Path) -> None:
    """OpenCode expects an mcp object with a local command array."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    config = json.loads(
        render_mcp_config(
            workspace_root,
            agent_id="opencode",
            python_executable="/python-for-test",
        )
    )

    server = config["mcp"]["synapse"]
    assert config["$schema"] == "https://opencode.ai/config.json"
    assert server["type"] == "local"
    assert server["enabled"] is True
    assert server["command"] == [
        "/python-for-test",
        "-m",
        "synapse",
        "serve",
        "--workspace",
        str(workspace_root),
    ]


def test_install_instruction_snippet_creates_and_is_idempotent(tmp_path: Path) -> None:
    """Instruction snippets are marker-wrapped and do not duplicate."""
    result = install_instruction_snippet("codex", tmp_path)
    second = install_instruction_snippet("codex", tmp_path)

    target = tmp_path / "AGENTS.md"
    content = target.read_text(encoding="utf-8")
    assert result.path == target
    assert result.status == "created"
    assert second.status == "unchanged"
    assert content.count(BEGIN_MARKER) == 1
    assert content.count(END_MARKER) == 1
    assert "synapse_orient" in content
    assert "synapse_inspect" in content
    assert "repository vocabulary" in content
    assert "never proof of absence" in content
    assert "synapse doctor --path . --agent codex" in content
    assert "use only if the index is stale or missing" not in content


def test_agent_snippets_differ_only_in_doctor_line() -> None:
    """One shared template renders every adapter snippet; only the doctor line differs."""
    bodies = set()
    for adapter in ADAPTERS.values():
        content = project_snippet(adapter.id)
        assert f"synapse doctor --path . --agent {adapter.id}" in content
        bodies.add("\n".join(line for line in content.splitlines() if "synapse doctor" not in line))
    assert len(bodies) == 1


def test_install_instruction_snippet_requires_force_to_replace_marker_block(
    tmp_path: Path,
) -> None:
    """Existing Synapse blocks are protected unless force is explicit."""
    target = tmp_path / "AGENTS.md"
    target.write_text(
        f"{BEGIN_MARKER}\nold content\n{END_MARKER}\n",
        encoding="utf-8",
    )

    with pytest.raises(FileExistsError):
        install_instruction_snippet("opencode", tmp_path)

    result = install_instruction_snippet("opencode", tmp_path, force=True)

    content = target.read_text(encoding="utf-8")
    assert result.status == "updated"
    assert "old content" not in content
    assert "synapse doctor --path . --agent opencode" in content


def test_remove_instruction_snippet_removes_only_marker_block(tmp_path: Path) -> None:
    """Uninstall removes the managed block while preserving surrounding content."""
    target = tmp_path / "AGENTS.md"
    install_instruction_snippet("codex", tmp_path)
    target.write_text(
        f"before\n\n{target.read_text(encoding='utf-8')}\nafter\n",
        encoding="utf-8",
    )

    result = remove_instruction_snippet("codex", tmp_path)

    content = target.read_text(encoding="utf-8")
    assert result.status == "removed"
    assert BEGIN_MARKER not in content
    assert END_MARKER not in content
    assert content == "before\n\nafter\n"


def test_remove_instruction_snippet_deletes_emptied_file(tmp_path: Path) -> None:
    """A file created solely for the managed instruction block is removed."""
    install_instruction_snippet("codex", tmp_path)

    result = remove_instruction_snippet("codex", tmp_path)

    assert result.status == "removed"
    assert not (tmp_path / "AGENTS.md").exists()


def test_remove_instruction_snippet_dry_run_writes_nothing(tmp_path: Path) -> None:
    """Dry-run reports the removal without touching the file."""
    install_instruction_snippet("codex", tmp_path)

    result = remove_instruction_snippet("codex", tmp_path, dry_run=True)

    assert result.status == "would-remove"
    assert (tmp_path / "AGENTS.md").exists()
