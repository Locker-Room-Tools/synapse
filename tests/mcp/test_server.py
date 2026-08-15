"""Tests for MCP server metadata."""

from pathlib import Path

import pytest

from synapse.mcp import server
from synapse.mcp.server import mcp


def test_server_instructions_advertise_synapse_first_flow() -> None:
    """The MCP handshake carries agent-facing Synapse-first instructions."""
    instructions = mcp.instructions

    assert instructions is not None
    assert "synapse_orient" in instructions
    assert "synapse_inspect" in instructions
    assert "repository vocabulary" in instructions
    assert "never proof of absence" in instructions
    assert "initialize the workspace automatically" in instructions.lower()


def test_server_instructions_carry_the_orchestration_contract() -> None:
    """The handshake states facet planning, small selection, gap closure, and stopping."""
    instructions = " ".join((mcp.instructions or "").split())

    assert "evidence facets" in instructions
    assert "4-8 discriminative terms" in instructions
    assert "2-3 initial facet-diverse anchors" in instructions
    assert "1-2 returned relation handles" in instructions
    assert "no budget parameter to raise" in instructions
    assert "verified, partial, or missing" in instructions
    assert "one bounded close attempt" in instructions
    assert "verified or unresolved" in instructions
    assert "broad repository search" in instructions
    assert "requested deliverables" in instructions
    assert "guard or recovery path" in instructions


def test_server_rejects_a_missing_workspace_before_starting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serving never creates cache state for a nonexistent workspace."""
    data_root = tmp_path / "data-root"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))
    monkeypatch.setattr(server.mcp, "run", lambda **_: pytest.fail("server started"))

    with pytest.raises(NotADirectoryError, match="Workspace is not a directory"):
        server.run(tmp_path / "missing")

    assert not data_root.exists()


def test_server_starts_stdio_without_initializing_new_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new workspace exposes ensure/status tools without eager initialization."""
    calls: list[object] = []
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def fake_configure(path: Path) -> Path:
        calls.append(("configure", path))
        return workspace

    def fake_run(*, transport: str) -> None:
        calls.append(("stdio", transport))

    monkeypatch.setattr(server, "configure_workspace", fake_configure)
    monkeypatch.setattr(server, "read_metadata", lambda path: None)
    monkeypatch.setattr(
        server,
        "ensure_watch_daemon",
        lambda path: pytest.fail("started daemon for new workspace"),
    )
    monkeypatch.setattr(server.mcp, "run", fake_run)

    server.run(workspace)

    assert calls == [
        ("configure", workspace),
        ("stdio", "stdio"),
    ]


def test_server_daemon_failure_prevents_stdio_for_initialized_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An initialized workspace retains the strict daemon freshness invariant."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(server, "configure_workspace", lambda path: workspace)
    monkeypatch.setattr(server, "read_metadata", lambda path: object())

    def fail_ensure(path: Path) -> None:
        raise RuntimeError("daemon unavailable")

    monkeypatch.setattr(server, "ensure_watch_daemon", fail_ensure)
    monkeypatch.setattr(server.mcp, "run", lambda **_: pytest.fail("server started"))

    with pytest.raises(RuntimeError, match="daemon unavailable"):
        server.run(workspace)


def test_server_auto_detects_workspace_when_global_config_has_no_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Portable global config delegates cwd repository discovery to workspace context."""
    calls: list[object] = []
    workspace = tmp_path / "repository"
    workspace.mkdir()

    def fake_configure(path: Path | None) -> Path:
        calls.append(("configure", path))
        return workspace

    monkeypatch.setattr(server, "configure_workspace", fake_configure)
    monkeypatch.setattr(server, "read_metadata", lambda path: None)
    monkeypatch.setattr(server.mcp, "run", lambda *, transport: calls.append(("run", transport)))

    server.run()

    assert calls == [("configure", None), ("run", "stdio")]
