"""Doctor checks for validating a Synapse installation through MCP."""

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from synapse.cli.adapters import get_adapter
from synapse.core.index import SymbolIndex
from synapse.core.indexing import index_workspace
from synapse.core.workspace import db_path, normalize_workspace_path

EXPECTED_TOOLS = {
    "synapse_get_definition",
    "synapse_get_dependencies",
    "synapse_get_file_outline",
    "synapse_get_symbol_context",
    "synapse_index_workspace",
    "synapse_search_symbols",
}


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """One doctor check result."""

    name: str
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Complete doctor report."""

    workspace_path: str
    agent: str | None
    checks: list[DoctorCheck]


async def _probe_mcp(workspace_root: Path) -> tuple[list[str], int]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "synapse", "serve", "--workspace", str(workspace_root)],
        cwd=str(workspace_root),
        env=dict(os.environ),
    )
    with anyio.fail_after(20):
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_response = await session.list_tools()
                tool_names = sorted(tool.name for tool in tools_response.tools)
                result = await session.call_tool(
                    "synapse_search_symbols",
                    {"query": "", "limit": 1},
                )
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        items = structured.get("items", [])
        if isinstance(items, list):
            return tool_names, len(items)
    content: Any = getattr(result, "content", [])
    if isinstance(content, list):
        return tool_names, len(content)
    return tool_names, 0


def run_doctor(path: str | Path = ".", agent: str | None = None) -> DoctorReport:
    """Run installation checks and return a structured report."""
    checks: list[DoctorCheck] = []
    workspace_root = normalize_workspace_path(path)

    try:
        import synapse

        version = getattr(synapse, "__version__", "unknown")
        checks.append(DoctorCheck("package", "ok", f"synapse {version} importable"))
    except Exception as exc:  # pragma: no cover - defensive startup check
        checks.append(DoctorCheck("package", "fail", f"package import failed: {exc}"))
        return DoctorReport(str(workspace_root), agent, checks)

    if agent is not None:
        try:
            adapter = get_adapter(agent)
            checks.append(DoctorCheck("agent", "ok", f"{adapter.display_name} adapter found"))
        except ValueError as exc:
            checks.append(DoctorCheck("agent", "fail", str(exc)))

    if not workspace_root.exists() or not workspace_root.is_dir():
        checks.append(DoctorCheck("workspace", "fail", f"{workspace_root} is not a directory"))
        return DoctorReport(str(workspace_root), agent, checks)
    checks.append(DoctorCheck("workspace", "ok", str(workspace_root)))

    try:
        stats = index_workspace(workspace_root)
        checks.append(
            DoctorCheck(
                "index",
                "ok",
                (
                    f"indexed={stats.indexed_files}, skipped={stats.skipped_files}, "
                    f"removed={stats.removed_files}"
                ),
            )
        )
    except Exception as exc:
        checks.append(DoctorCheck("index", "fail", f"indexing failed: {exc}"))
        return DoctorReport(str(workspace_root), agent, checks)

    indexed_files = len(SymbolIndex(db_path(workspace_root)).list_indexed_files())
    if indexed_files == 0:
        checks.append(DoctorCheck("indexed_files", "warn", "no supported source files indexed"))
    else:
        checks.append(DoctorCheck("indexed_files", "ok", f"{indexed_files} files indexed"))

    try:
        tool_names, result_count = anyio.run(_probe_mcp, workspace_root)
        missing_tools = sorted(EXPECTED_TOOLS - set(tool_names))
        if missing_tools:
            checks.append(
                DoctorCheck("mcp_tools", "fail", f"missing tools: {', '.join(missing_tools)}")
            )
        else:
            checks.append(DoctorCheck("mcp_tools", "ok", f"{len(tool_names)} tools advertised"))
        checks.append(DoctorCheck("mcp_call", "ok", f"search call returned {result_count} items"))
    except Exception as exc:
        checks.append(DoctorCheck("mcp", "fail", f"MCP probe failed: {exc}"))

    return DoctorReport(str(workspace_root), agent, checks)


def has_failures(report: DoctorReport) -> bool:
    """Return whether the report contains a hard failure."""
    return any(check.status == "fail" for check in report.checks)


def report_to_json(report: DoctorReport) -> str:
    """Return a JSON representation of a doctor report."""
    return json.dumps(asdict(report), indent=2) + "\n"


def format_report(report: DoctorReport) -> str:
    """Return a human-readable doctor report."""
    lines = ["Synapse doctor", "", f"Workspace: {report.workspace_path}"]
    if report.agent is not None:
        lines.append(f"Agent: {report.agent}")
    lines.append("")
    for check in report.checks:
        lines.append(f"[{check.status}] {check.name}: {check.message}")
    return "\n".join(lines) + "\n"
