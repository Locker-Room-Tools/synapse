"""Tests for MCP server metadata."""

from synapse.mcp.server import mcp


def test_server_instructions_advertise_synapse_first_flow() -> None:
    """The MCP handshake carries agent-facing Synapse-first instructions."""
    instructions = mcp.instructions

    assert instructions is not None
    assert "synapse_get_definition" in instructions
    assert "synapse_find_references" in instructions
    assert "BEFORE grep" in instructions
