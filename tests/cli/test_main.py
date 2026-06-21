"""Tests for CLI argument parsing and dispatch."""

import json
from pathlib import Path

import pytest

from synapse.cli import main as cli_main
from synapse.core.indexing import IndexStats


def test_index_command_dispatches_and_prints_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The index subcommand dispatches to the indexing use case."""
    monkeypatch.setattr(
        cli_main,
        "index_workspace",
        lambda path, *, force=False: IndexStats(str(path), 1, 2, 3, 4, 5, ["python"]),
    )

    exit_code = cli_main.main(["index", "."])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Indexed files: 1" in captured.out
    assert "Stored symbols: 5" in captured.out


def test_index_command_forwards_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The index subcommand can force a full reparse of unchanged files."""
    seen: dict[str, object] = {}

    def fake_index_workspace(path: str, *, force: bool = False) -> IndexStats:
        seen["path"] = path
        seen["force"] = force
        return IndexStats(str(path), 1, 0, 0, 1, 1, ["tsx"])

    monkeypatch.setattr(cli_main, "index_workspace", fake_index_workspace)

    exit_code = cli_main.main(["index", ".", "--force"])

    assert exit_code == 0
    assert seen == {"path": ".", "force": True}


def test_setup_command_prints_workspace_details(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The setup subcommand reports next steps without writing instructions by default."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setattr(
        cli_main,
        "index_workspace",
        lambda path, *, force=False: IndexStats(str(path), 1, 0, 0, 1, 1, ["python"]),
    )
    monkeypatch.setattr(cli_main, "_detect_workspace_root", lambda path: tmp_path)

    exit_code = cli_main.main(["setup", "codex", "--path", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Synapse workspace initialized." in captured.out
    assert "Repository files changed: none" in captured.out
    assert "synapse doctor --path" in captured.out
    assert not (tmp_path / "AGENTS.md").exists()


def test_setup_command_can_write_agent_instructions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Instruction writes are explicit and use the adapter default target."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setattr(
        cli_main,
        "index_workspace",
        lambda path, *, force=False: IndexStats(str(path), 1, 0, 0, 1, 1, ["python"]),
    )
    monkeypatch.setattr(cli_main, "_detect_workspace_root", lambda path: tmp_path)

    exit_code = cli_main.main(["setup", "codex", "--path", str(tmp_path), "--write-instructions"])

    captured = capsys.readouterr()
    instructions = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert exit_code == 0
    assert "Repository files changed:" in captured.out
    assert "synapse doctor --path . --agent codex" in instructions


def test_mcp_install_can_write_a_template(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The mcp install subcommand materializes a workspace-pinned config."""
    output_path = tmp_path / "codex.json"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    exit_code = cli_main.main(
        [
            "mcp",
            "install",
            "codex",
            "--workspace",
            str(workspace_root),
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    config = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert config["mcpServers"]["synapse"]["args"][-1] == str(workspace_root)
    assert "Wrote MCP config template" in captured.out


def test_mcp_install_writes_opencode_template(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The OpenCode adapter materializes OpenCode's mcp config shape."""
    output_path = tmp_path / "opencode.json"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    exit_code = cli_main.main(
        [
            "mcp",
            "install",
            "opencode",
            "--workspace",
            str(workspace_root),
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    config = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert config["mcp"]["synapse"]["type"] == "local"
    assert config["mcp"]["synapse"]["enabled"] is True
    assert config["mcp"]["synapse"]["command"][-2:] == ["--workspace", str(workspace_root)]
    assert "Wrote MCP config template" in captured.out


def test_mcp_install_refuses_to_overwrite_without_force(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Existing config files are protected unless --force is used."""
    output_path = tmp_path / "codex.json"
    output_path.write_text("existing\n", encoding="utf-8")

    exit_code = cli_main.main(["mcp", "install", "codex", "--output", str(output_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "already exists" in captured.err
    assert output_path.read_text(encoding="utf-8") == "existing\n"


def test_doctor_validates_mcp_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Doctor indexes a workspace and proves the MCP server can answer a tool call."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "sample.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    exit_code = cli_main.main(
        ["doctor", "--path", str(workspace_root), "--agent", "codex", "--json"]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    statuses = {check["name"]: check["status"] for check in report["checks"]}
    assert exit_code == 0
    assert statuses["mcp_tools"] == "ok"
    assert statuses["mcp_call"] == "ok"
