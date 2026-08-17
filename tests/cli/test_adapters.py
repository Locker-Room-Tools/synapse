"""Tests for agent adapter config and instruction helpers."""

import json
import tomllib
from pathlib import Path

from synapse.cli.adapters import (
    ADAPTERS,
    LEGACY_BEGIN_MARKER,
    LEGACY_END_MARKER,
    install_instruction_snippet,
    project_snippet,
    remove_instruction_snippet,
    render_mcp_config,
)

PROJECT_HEADING = "## Synapse Context Engine (use first)"


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
    """Instruction snippets are heading-anchored, markup-free, and do not duplicate."""
    result = install_instruction_snippet("codex", tmp_path)
    second = install_instruction_snippet("codex", tmp_path)

    target = tmp_path / "AGENTS.md"
    content = target.read_text(encoding="utf-8")
    assert result.path == target
    assert result.status == "created"
    assert second.status == "unchanged"
    assert content.count(PROJECT_HEADING) == 1
    assert "<!--" not in content
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


def test_install_instruction_snippet_replaces_a_managed_block_without_force(
    tmp_path: Path,
) -> None:
    """A block Synapse owns is replaced in place; several adapters share one file."""
    target = tmp_path / "AGENTS.md"
    target.write_text(
        f"{PROJECT_HEADING}\n\nold content\n",
        encoding="utf-8",
    )

    result = install_instruction_snippet("opencode", tmp_path)

    content = target.read_text(encoding="utf-8")
    assert result.status == "updated"
    assert "old content" not in content
    assert "synapse doctor --path . --agent opencode" in content


def test_unmanaged_content_is_never_replaced_by_a_block_install(tmp_path: Path) -> None:
    """Text Synapse does not own is preserved; the block is appended after it."""
    target = tmp_path / "AGENTS.md"
    target.write_text("# House rules\n\nhand written\n", encoding="utf-8")

    result = install_instruction_snippet("opencode", tmp_path)

    content = target.read_text(encoding="utf-8")
    assert result.status == "updated"
    assert "hand written" in content
    assert content.count(PROJECT_HEADING) == 1


def test_adapters_sharing_one_file_keep_a_single_block(tmp_path: Path) -> None:
    """AGENTS.md holds one Synapse block no matter how many adapters install into it."""
    target = tmp_path / "AGENTS.md"

    for agent in ("codex", "opencode", "kimi", "amp", "droid"):
        install_instruction_snippet(agent, tmp_path)

    content = target.read_text(encoding="utf-8")
    assert content.count(PROJECT_HEADING) == 1
    assert "synapse doctor --path . --agent droid" in content
    assert "synapse doctor --path . --agent codex" not in content


def test_both_copilot_adapters_keep_a_single_block(tmp_path: Path) -> None:
    """The CLI and VS Code Copilot adapters share .github/copilot-instructions.md."""
    install_instruction_snippet("copilot", tmp_path)
    install_instruction_snippet("copilot-vscode", tmp_path)

    content = (tmp_path / ".github/copilot-instructions.md").read_text(encoding="utf-8")
    assert content.count(PROJECT_HEADING) == 1


def test_install_instruction_snippet_migrates_a_legacy_marker_block(
    tmp_path: Path,
) -> None:
    """A pre-0.5.1 marker-delimited block is replaced in place and the markers stripped."""
    target = tmp_path / "AGENTS.md"
    target.write_text(
        f"# Guide\n\n{LEGACY_BEGIN_MARKER}\nold content\n{LEGACY_END_MARKER}\n\n## After\n",
        encoding="utf-8",
    )

    result = install_instruction_snippet("codex", tmp_path, force=True)

    content = target.read_text(encoding="utf-8")
    assert result.status == "updated"
    assert "old content" not in content
    assert LEGACY_BEGIN_MARKER not in content
    assert LEGACY_END_MARKER not in content
    assert content.startswith("# Guide\n")
    assert content.endswith("## After\n")
    assert PROJECT_HEADING in content


def test_remove_instruction_snippet_removes_only_managed_block(tmp_path: Path) -> None:
    """Uninstall removes the managed heading block while preserving surrounding content."""
    target = tmp_path / "AGENTS.md"
    install_instruction_snippet("codex", tmp_path)
    target.write_text(
        f"# Guide\n\nbefore\n\n{target.read_text(encoding='utf-8')}\n## After\n\nafter\n",
        encoding="utf-8",
    )

    result = remove_instruction_snippet("codex", tmp_path)

    content = target.read_text(encoding="utf-8")
    assert result.status == "removed"
    assert PROJECT_HEADING not in content
    assert content == "# Guide\n\nbefore\n\n## After\n\nafter\n"


def test_remove_instruction_snippet_removes_a_legacy_marker_block(tmp_path: Path) -> None:
    """Uninstall still recognizes and strips a pre-0.5.1 marker-delimited block."""
    target = tmp_path / "AGENTS.md"
    target.write_text(
        f"before\n\n{LEGACY_BEGIN_MARKER}\nold content\n{LEGACY_END_MARKER}\n\nafter\n",
        encoding="utf-8",
    )

    result = remove_instruction_snippet("codex", tmp_path)

    content = target.read_text(encoding="utf-8")
    assert result.status == "removed"
    assert LEGACY_BEGIN_MARKER not in content
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
