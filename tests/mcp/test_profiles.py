"""Tests for profile-based MCP tool exposure."""

from pathlib import Path

import anyio
import pytest
from mcp.server.fastmcp import FastMCP

from synapse.cli.doctor import expected_tools
from synapse.mcp.profiles import ToolProfile, tool_names_for_profile
from synapse.mcp.server import register_tools

DEFAULT_TOOLS = {
    "synapse_ensure_workspace",
    "synapse_query_context",
    "synapse_get_definition",
    "synapse_get_symbol_context",
    "synapse_find_references",
}


def test_default_profile_is_the_minimal_coding_agent_surface() -> None:
    assert set(tool_names_for_profile(ToolProfile.DEFAULT)) == DEFAULT_TOOLS


def test_full_profile_contains_every_tool() -> None:
    full = set(tool_names_for_profile(ToolProfile.FULL))
    assert full >= DEFAULT_TOOLS
    assert len(full) == 18


def test_doctor_expectations_derive_from_the_profile_registry() -> None:
    assert expected_tools(ToolProfile.DEFAULT) == set(tool_names_for_profile(ToolProfile.DEFAULT))
    assert expected_tools(ToolProfile.FULL) == set(tool_names_for_profile(ToolProfile.FULL))


def _registered_names(server: FastMCP) -> set[str]:
    return {tool.name for tool in anyio.run(server.list_tools)}


def test_register_tools_exposes_exactly_the_profile_surface() -> None:
    server = FastMCP("synapse-test")
    register_tools(server, ToolProfile.DEFAULT)
    assert _registered_names(server) == DEFAULT_TOOLS


def test_register_tools_is_idempotent_and_can_widen_to_full() -> None:
    server = FastMCP("synapse-test")
    register_tools(server, ToolProfile.DEFAULT)
    register_tools(server, ToolProfile.DEFAULT)
    register_tools(server, ToolProfile.FULL)
    assert _registered_names(server) == set(tool_names_for_profile(ToolProfile.FULL))


def test_query_context_result_has_single_wire_representation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The budget-bound string is not duplicated into structuredContent."""
    from synapse.mcp import tools

    monkeypatch.setattr(tools, "require_workspace_ready", lambda path: tmp_path)
    monkeypatch.setattr(
        tools,
        "query_context",
        lambda index, query, *, workspace_root: '{"seeds":[],"coverage":{}}',
    )
    server = FastMCP("synapse-test")
    register_tools(server, ToolProfile.DEFAULT)

    async def call() -> list[object]:
        result = await server.call_tool(
            "synapse_query_context", {"question": "anything", "workspace_path": str(tmp_path)}
        )
        assert not isinstance(result, dict)
        return list(result)

    blocks = anyio.run(call)
    assert len(blocks) == 1
    assert getattr(blocks[0], "text", "") == '{"seeds":[],"coverage":{}}'
