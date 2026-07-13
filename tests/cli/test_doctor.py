"""Tests for doctor checks (fast paths; the MCP probe is stubbed)."""

from pathlib import Path

import pytest

from synapse.cli import doctor
from synapse.cli.doctor import (
    EXPECTED_TOOLS,
    DoctorReport,
    format_report,
    has_failures,
    report_to_json,
    run_doctor,
)


def _check_status(report: DoctorReport, name: str) -> str:
    return next(check.status for check in report.checks if check.name == name)


def _stub_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tools: set[str] | None = None,
    instructions: str | None = "use synapse tools",
    error: Exception | None = None,
) -> None:
    async def fake_probe(_workspace_root: Path) -> tuple[list[str], int, str | None]:
        if error is not None:
            raise error
        return sorted(tools if tools is not None else EXPECTED_TOOLS), 0, instructions

    monkeypatch.setattr(doctor, "_probe_mcp", fake_probe)


def test_doctor_fails_fast_on_missing_workspace(tmp_path: Path) -> None:
    """A nonexistent workspace is a hard failure and stops further checks."""
    report = run_doctor(tmp_path / "missing")

    assert _check_status(report, "workspace") == "fail"
    assert has_failures(report)
    assert not any(check.name == "index" for check in report.checks)


def test_doctor_flags_unknown_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unsupported agent id produces an agent failure check."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    _stub_probe(monkeypatch)
    (tmp_path / "app.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    report = run_doctor(tmp_path, agent="unknown-agent")

    assert _check_status(report, "agent") == "fail"
    assert has_failures(report)


def test_doctor_warns_on_missing_agent_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A known agent with no installed config or snippet yields warnings, not failures."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    _stub_probe(monkeypatch)
    (tmp_path / "app.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    report = run_doctor(tmp_path, agent="claude-code", scope="project")

    assert _check_status(report, "agent") == "ok"
    assert _check_status(report, "mcp_config") == "warn"
    assert _check_status(report, "instructions") == "warn"
    assert _check_status(report, "index") == "ok"
    assert not has_failures(report)


def test_doctor_fails_when_tools_are_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server that omits expected tools is reported as a hard failure."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    _stub_probe(monkeypatch, tools=set(sorted(EXPECTED_TOOLS)[:3]), instructions=None)
    (tmp_path / "app.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    report = run_doctor(tmp_path)

    assert _check_status(report, "mcp_tools") == "fail"
    assert _check_status(report, "server_instructions") == "warn"
    assert has_failures(report)


def test_doctor_reports_probe_crash_as_mcp_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crashing MCP probe becomes a structured mcp failure check."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    _stub_probe(monkeypatch, error=RuntimeError("server exploded"))
    (tmp_path / "app.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    report = run_doctor(tmp_path)

    assert _check_status(report, "mcp") == "fail"
    assert "server exploded" in next(c.message for c in report.checks if c.name == "mcp")


def test_doctor_report_serialization_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reports render as JSON and human-readable text with every check present."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    _stub_probe(monkeypatch)
    (tmp_path / "app.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    report = run_doctor(tmp_path)

    json_text = report_to_json(report)
    human_text = format_report(report)
    for check in report.checks:
        assert check.name in json_text
        assert check.name in human_text
    assert not has_failures(report)
