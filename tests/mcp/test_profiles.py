"""Tests for profile-based MCP tool exposure."""

from pathlib import Path

import anyio
import pytest
from mcp.server.fastmcp import FastMCP

from synapse.cli.doctor import expected_tools
from synapse.mcp.profiles import ToolProfile, tool_names_for_profile
from synapse.mcp.server import register_tools

DEFAULT_TOOLS = {
    "synapse_orient",
    "synapse_inspect",
}


def test_default_profile_is_the_minimal_coding_agent_surface() -> None:
    assert set(tool_names_for_profile(ToolProfile.DEFAULT)) == DEFAULT_TOOLS


def test_full_profile_contains_every_tool() -> None:
    full = set(tool_names_for_profile(ToolProfile.FULL))
    assert full >= DEFAULT_TOOLS
    assert "synapse_ensure_workspace" in full
    assert "synapse_query_context" not in full
    assert len(full) == 19


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


def test_navigation_results_have_single_wire_representation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The budget-bound string is not duplicated into structuredContent."""
    from synapse.mcp import tools

    monkeypatch.setattr(tools, "_navigation_workspace", lambda path=".": tmp_path)
    monkeypatch.setattr(
        tools,
        "orient_workspace",
        lambda index, request, *, workspace_root: '{"matches":[],"coverage":{}}',
    )
    server = FastMCP("synapse-test")
    register_tools(server, ToolProfile.DEFAULT)

    async def call() -> list[object]:
        result = await server.call_tool(
            "synapse_orient", {"terms": ["anything"], "workspace_path": str(tmp_path)}
        )
        assert not isinstance(result, dict)
        return list(result)

    blocks = anyio.run(call)
    assert len(blocks) == 1
    assert getattr(blocks[0], "text", "") == '{"matches":[],"coverage":{}}'
