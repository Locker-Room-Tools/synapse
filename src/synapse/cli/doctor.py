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

from synapse.cli.adapters import (
    AgentAdapter,
    get_adapter,
    has_managed_instruction_block,
    resolve_instruction_path,
)
from synapse.cli.installer import config_has_mcp_server, resolve_config_path
from synapse.core.index import SymbolIndex
from synapse.core.indexing import index_workspace
from synapse.core.watch.state import watch_status_payload
from synapse.core.workspace import db_path, normalize_workspace_path
from synapse.mcp.profiles import ToolProfile, tool_names_for_profile


def expected_tools(profile: ToolProfile) -> set[str]:
    """Expected advertised tool names, derived from the single profile registry."""
    return set(tool_names_for_profile(profile))


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


async def _probe_mcp(
    workspace_root: Path, profile: ToolProfile = ToolProfile.DEFAULT
) -> tuple[list[str], int, str | None]:
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "synapse",
            "serve",
            "--workspace",
            str(workspace_root),
            "--profile",
            profile.value,
        ],
        cwd=str(workspace_root),
        env=dict(os.environ),
    )
    with anyio.fail_after(20):
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                instructions = getattr(initialized, "instructions", None)
                tools_response = await session.list_tools()
                tool_names = sorted(tool.name for tool in tools_response.tools)
                result = await session.call_tool(
                    "synapse_orient",
                    {"terms": [], "token_budget": 400},
                )
    content: Any = getattr(result, "content", [])
    if isinstance(content, list):
        payload_chars = sum(len(getattr(item, "text", "")) for item in content)
        return tool_names, payload_chars, instructions
    return tool_names, 0, instructions


def _check_package() -> DoctorCheck:
    try:
        import synapse

        version = getattr(synapse, "__version__", "unknown")
        return DoctorCheck("package", "ok", f"synapse {version} importable")
    except Exception as exc:  # pragma: no cover - defensive startup check
        return DoctorCheck("package", "fail", f"package import failed: {exc}")


def _check_agent(agent: str) -> tuple[AgentAdapter | None, DoctorCheck]:
    try:
        adapter = get_adapter(agent)
    except ValueError as exc:
        return None, DoctorCheck("agent", "fail", str(exc))
    return adapter, DoctorCheck("agent", "ok", f"{adapter.display_name} adapter found")


def _check_workspace(workspace_root: Path) -> DoctorCheck:
    if not workspace_root.exists() or not workspace_root.is_dir():
        return DoctorCheck("workspace", "fail", f"{workspace_root} is not a directory")
    return DoctorCheck("workspace", "ok", str(workspace_root))


def _check_mcp_config(
    agent: str,
    adapter: AgentAdapter,
    workspace_root: Path,
    scope: str | None,
) -> DoctorCheck:
    resolved_scope = scope or adapter.default_scope
    try:
        config_path = resolve_config_path(adapter, workspace_root, resolved_scope)
        if config_has_mcp_server(agent, workspace_root, scope=resolved_scope):
            return DoctorCheck("mcp_config", "ok", f"synapse entry found in {config_path}")
        return DoctorCheck("mcp_config", "warn", f"synapse entry not found in {config_path}")
    except ValueError as exc:
        return DoctorCheck("mcp_config", "warn", str(exc))


def _check_instructions(agent: str, workspace_root: Path) -> DoctorCheck:
    instruction_path = resolve_instruction_path(agent, workspace_root)
    if not instruction_path.exists():
        return DoctorCheck(
            "instructions", "warn", f"instruction file not found at {instruction_path}"
        )
    instruction_text = instruction_path.read_text(encoding="utf-8")
    if has_managed_instruction_block(instruction_text):
        return DoctorCheck(
            "instructions", "ok", f"Synapse instruction block found in {instruction_path}"
        )
    return DoctorCheck(
        "instructions", "warn", f"Synapse instruction block not found in {instruction_path}"
    )


def _check_index(workspace_root: Path) -> DoctorCheck:
    try:
        stats = index_workspace(workspace_root)
    except Exception as exc:
        return DoctorCheck("index", "fail", f"indexing failed: {exc}")
    return DoctorCheck(
        "index",
        "ok",
        (
            f"indexed={stats.indexed_files}, skipped={stats.skipped_files}, "
            f"removed={stats.removed_files}"
        ),
    )


def _check_indexed_files(workspace_root: Path) -> DoctorCheck:
    indexed_files = len(SymbolIndex(db_path(workspace_root)).list_indexed_files())
    if indexed_files == 0:
        return DoctorCheck("indexed_files", "warn", "no supported source files indexed")
    return DoctorCheck("indexed_files", "ok", f"{indexed_files} files indexed")


def _check_watch(workspace_root: Path) -> DoctorCheck:
    watch_status = watch_status_payload(workspace_root)
    if watch_status["running"] and not watch_status["degraded"]:
        return DoctorCheck("watch", "ok", f"daemon running via {watch_status['backend']}")
    return DoctorCheck(
        "watch",
        "fail",
        "watch daemon is not healthy; Synapse cannot guarantee a fresh index",
    )


def _check_mcp_probe(workspace_root: Path) -> list[DoctorCheck]:
    try:
        tool_names, payload_chars, instructions = anyio.run(_probe_mcp, workspace_root)
    except Exception as exc:
        return [DoctorCheck("mcp", "fail", f"MCP probe failed: {exc}")]
    checks: list[DoctorCheck] = []
    expected = expected_tools(ToolProfile.DEFAULT)
    advertised = set(tool_names)
    missing_tools = sorted(expected - advertised)
    unexpected_tools = sorted(advertised - expected)
    if missing_tools:
        checks.append(
            DoctorCheck("mcp_tools", "fail", f"missing tools: {', '.join(missing_tools)}")
        )
    elif unexpected_tools:
        checks.append(
            DoctorCheck(
                "mcp_tools",
                "fail",
                f"unexpected tools in default profile: {', '.join(unexpected_tools)}",
            )
        )
    else:
        checks.append(DoctorCheck("mcp_tools", "ok", f"{len(tool_names)} tools advertised"))
    if instructions:
        checks.append(DoctorCheck("server_instructions", "ok", "server instructions advertised"))
    else:
        checks.append(DoctorCheck("server_instructions", "warn", "server instructions missing"))
    if payload_chars > 0:
        checks.append(DoctorCheck("mcp_call", "ok", f"orientation returned {payload_chars} chars"))
    else:
        checks.append(DoctorCheck("mcp_call", "fail", "orientation returned no content"))
    return checks


def run_doctor(
    path: str | Path = ".",
    agent: str | None = None,
    *,
    scope: str | None = None,
) -> DoctorReport:
    """Run installation checks and return a structured report."""
    checks: list[DoctorCheck] = []
    workspace_root = normalize_workspace_path(path)

    package_check = _check_package()
    checks.append(package_check)
    if package_check.status == "fail":
        return DoctorReport(str(workspace_root), agent, checks)

    adapter = None
    if agent is not None:
        adapter, agent_check = _check_agent(agent)
        checks.append(agent_check)

    workspace_check = _check_workspace(workspace_root)
    checks.append(workspace_check)
    if workspace_check.status == "fail":
        return DoctorReport(str(workspace_root), agent, checks)

    if agent is not None and adapter is not None:
        checks.append(_check_mcp_config(agent, adapter, workspace_root, scope))
        checks.append(_check_instructions(agent, workspace_root))

    index_check = _check_index(workspace_root)
    checks.append(index_check)
    if index_check.status == "fail":
        return DoctorReport(str(workspace_root), agent, checks)

    checks.append(_check_indexed_files(workspace_root))
    checks.extend(_check_mcp_probe(workspace_root))
    checks.append(_check_watch(workspace_root))
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
