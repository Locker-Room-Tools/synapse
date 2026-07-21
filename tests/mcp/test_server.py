"""Tests for MCP server metadata."""

from pathlib import Path

import pytest

from synapse.mcp import server
from synapse.mcp.server import mcp


def test_server_instructions_advertise_synapse_first_flow() -> None:
    """The MCP handshake carries agent-facing Synapse-first instructions."""
    instructions = mcp.instructions

    assert instructions is not None
    assert "synapse_get_definition" in instructions
    assert "synapse_find_references" in instructions
    assert "BEFORE grep" in instructions


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
