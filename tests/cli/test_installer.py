"""Tests for MCP config install and uninstall helpers."""

import json
import tomllib
from pathlib import Path

import pytest

from synapse.cli.installer import (
    MANAGED_TOML_BEGIN,
    config_has_mcp_server,
    install_mcp_server,
    uninstall_mcp_server,
)


def test_json_install_merges_and_preserves_sibling_servers(tmp_path: Path) -> None:
    """JSON installs update only the synapse server entry."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "opencode.json"
    target.write_text(
        json.dumps({"mcp": {"other": {"command": ["tool"]}}, "theme": "dark"}),
        encoding="utf-8",
    )

    result = install_mcp_server(
        "opencode",
        workspace,
        scope="project",
        python_executable="/python-for-test",
    )
    second = install_mcp_server(
        "opencode",
        workspace,
        scope="project",
        python_executable="/python-for-test",
    )

    config = json.loads(target.read_text(encoding="utf-8"))
    assert result.action == "updated"
    assert second.action == "unchanged"
    assert config["theme"] == "dark"
    assert config["mcp"]["other"] == {"command": ["tool"]}
    assert config["mcp"]["synapse"]["command"][-2:] == ["--workspace", str(workspace)]
    assert config_has_mcp_server("opencode", workspace, scope="project")


def test_json_install_refuses_to_overwrite_different_synapse_entry(
    tmp_path: Path,
) -> None:
    """Existing synapse entries are protected unless force is explicit."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / ".mcp.json"
    target.write_text(
        json.dumps({"mcpServers": {"synapse": {"command": "different"}}}),
        encoding="utf-8",
    )

    with pytest.raises(FileExistsError):
        install_mcp_server("claude-code", workspace, scope="project")

    result = install_mcp_server(
        "claude-code",
        workspace,
        scope="project",
        force=True,
        python_executable="/python-for-test",
    )

    config = json.loads(target.read_text(encoding="utf-8"))
    assert result.action == "updated"
    assert config["mcpServers"]["synapse"]["command"] == "/python-for-test"


def test_json_uninstall_removes_only_synapse_entry(tmp_path: Path) -> None:
    """JSON uninstall preserves sibling server entries."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / ".mcp.json"
    target.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "synapse": {"command": "old"},
                    "other": {"command": "tool"},
                }
            }
        ),
        encoding="utf-8",
    )

    result = uninstall_mcp_server("claude-code", workspace, scope="project")

    config = json.loads(target.read_text(encoding="utf-8"))
    assert result.action == "removed"
    assert "synapse" not in config["mcpServers"]
    assert config["mcpServers"]["other"] == {"command": "tool"}


def test_toml_install_uses_managed_block_and_uninstall_strips_it(
    tmp_path: Path,
) -> None:
    """Codex TOML installs are marker-managed and reversible."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / ".codex" / "config.toml"
    target.parent.mkdir()
    target.write_text('[profile]\nname = "default"\n', encoding="utf-8")

    result = install_mcp_server(
        "codex",
        workspace,
        scope="project",
        python_executable="/python-for-test",
    )
    second = install_mcp_server(
        "codex",
        workspace,
        scope="project",
        python_executable="/python-for-test",
    )

    text = target.read_text(encoding="utf-8")
    config = tomllib.loads(text)
    assert result.action == "updated"
    assert second.action == "unchanged"
    assert MANAGED_TOML_BEGIN in text
    assert config["profile"]["name"] == "default"
    assert config["mcp_servers"]["synapse"]["command"] == "/python-for-test"

    uninstall = uninstall_mcp_server("codex", workspace, scope="project")

    assert uninstall.action == "removed"
    assert target.read_text(encoding="utf-8") == '[profile]\nname = "default"\n'


def test_toml_dry_run_writes_nothing(tmp_path: Path) -> None:
    """Dry-run returns the would-be TOML without creating the config file."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = install_mcp_server("codex", workspace, scope="project", dry_run=True)

    assert result.action == "would-create"
    assert MANAGED_TOML_BEGIN in result.content_preview
    assert not (workspace / ".codex" / "config.toml").exists()


def test_toml_uninstall_deletes_emptied_file(tmp_path: Path) -> None:
    """A Codex config created solely for Synapse is removed on uninstall."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    install_mcp_server("codex", workspace, scope="project")

    result = uninstall_mcp_server("codex", workspace, scope="project")

    assert result.action == "removed"
    assert not (workspace / ".codex" / "config.toml").exists()
