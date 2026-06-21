"""Tests for agent adapter config and instruction helpers."""

import json
from pathlib import Path

import pytest

from synapse.cli.adapters import (
    BEGIN_MARKER,
    END_MARKER,
    adapter_choices,
    install_instruction_snippet,
    render_mcp_config,
)


def test_render_mcp_config_pins_workspace_for_mcp_servers_agents(tmp_path: Path) -> None:
    """Claude Code and Codex use the workspace-pinned mcpServers config shape."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    for agent_id in ("claude-code", "codex"):
        config = json.loads(
            render_mcp_config(
                workspace_root,
                agent_id=agent_id,
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

    assert set(adapter_choices()) == {"claude-code", "codex", "opencode"}
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
    assert "synapse doctor --path . --agent codex" in content


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
