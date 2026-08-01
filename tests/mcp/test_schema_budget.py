"""The default MCP surface is exactly two tools within a hard schema token budget.

Iteration 6 baseline: five tools at roughly 1,507 estimated tokens. The
navigation surface must stay at or under 700 estimated tokens for the wire
schema an agent loads on every session (names, descriptions, input schemas).
"""

import json

import anyio
from mcp.server.fastmcp import FastMCP

from synapse.core.navigation import estimate_tokens
from synapse.mcp.profiles import ToolProfile
from synapse.mcp.server import register_tools

SCHEMA_TOKEN_BUDGET = 700


def _default_schema() -> tuple[int, str]:
    server = FastMCP("schema-probe")
    register_tools(server, ToolProfile.DEFAULT)
    tools = anyio.run(server.list_tools)
    serialized = json.dumps(
        [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
            }
            for tool in tools
        ],
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    )
    return len(tools), serialized


def test_default_schema_is_exactly_two_tools_within_budget() -> None:
    tool_count, serialized = _default_schema()
    assert tool_count == 2
    assert estimate_tokens(serialized) <= SCHEMA_TOKEN_BUDGET, (
        f"default tool schema is {estimate_tokens(serialized)} estimated tokens; "
        f"budget is {SCHEMA_TOKEN_BUDGET}"
    )
