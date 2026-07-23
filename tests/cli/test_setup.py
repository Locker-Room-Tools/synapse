"""Tests for the unified Synapse setup workflow."""

from pathlib import Path

import pytest

import synapse.cli.main as cli_main
from synapse.cli.adapters import BEGIN_MARKER, END_MARKER, InstructionInstallResult
from synapse.cli.doctor import DoctorCheck, DoctorReport
from synapse.cli.installer import (
    MANAGED_TOML_BEGIN,
    MANAGED_TOML_END,
    InstallResult,
    install_mcp_server,
)
from synapse.core.indexing import IndexStats
from synapse.core.watch.state import WatchStatus
from synapse.core.workspace import workspace_id


def _stats(workspace: Path) -> IndexStats:
    return IndexStats(str(workspace), 1, 0, 0, 0, 1, 2, ["python"])


def _healthy_status(workspace: Path) -> WatchStatus:
    return WatchStatus(
        workspace_path=str(workspace),
        workspace_id=workspace_id(workspace),
        running=True,
        backend="polling",
        degraded=False,
        pending=0,
        pid=1234,
        started_at=None,
        stopped_at=None,
        last_event_ts=None,
        last_full_sweep_ts=None,
        last_reconcile_started_at=None,
        last_reconcile_finished_at=None,
        errors_count=0,
        errors=[],
    )


def _stub_successful_runtime(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
) -> None:
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ())
    monkeypatch.setattr(cli_main, "index_workspace", lambda path: _stats(workspace))
    monkeypatch.setattr(cli_main, "ensure_watch_daemon", lambda path: _healthy_status(workspace))
    monkeypatch.setattr(
        cli_main,
        "run_doctor",
        lambda path, *, agent, scope: DoctorReport(str(path), agent, []),
    )


def test_setup_runs_installation_steps_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup performs grammar, index, config, instructions, daemon, then doctor."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    calls: list[object] = []
    config_path = workspace / ".codex" / "config.toml"
    instruction_path = workspace / "AGENTS.md"

    def fake_install_grammars() -> tuple[str, ...]:
        calls.append("grammars")
        return ("python",)

    def fake_index(path: Path) -> IndexStats:
        calls.append(("index", path))
        return _stats(workspace)

    def fake_install_mcp(
        agent: str,
        path: Path,
        *,
        scope: str,
        force: bool,
    ) -> InstallResult:
        calls.append(("mcp", agent, path, scope, force))
        return InstallResult(config_path, "created", "")

    def fake_install_instructions(
        agent: str,
        path: Path,
        *,
        output_path: str | Path | None,
        force: bool,
    ) -> InstructionInstallResult:
        calls.append(("instructions", agent, path, output_path, force))
        return InstructionInstallResult(instruction_path, "created")

    def fake_ensure_daemon(path: Path) -> WatchStatus:
        calls.append(("daemon", path))
        return _healthy_status(workspace)

    def fake_doctor(
        path: Path,
        *,
        agent: str,
        scope: str,
    ) -> DoctorReport:
        calls.append(("doctor", path, agent, scope))
        return DoctorReport(str(path), agent, [])

    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ("python",))
    monkeypatch.setattr(cli_main, "install_grammars", fake_install_grammars)
    monkeypatch.setattr(cli_main, "index_workspace", fake_index)
    monkeypatch.setattr(cli_main, "install_mcp_server", fake_install_mcp)
    monkeypatch.setattr(cli_main, "install_instruction_snippet", fake_install_instructions)
    monkeypatch.setattr(cli_main, "ensure_watch_daemon", fake_ensure_daemon)
    monkeypatch.setattr(cli_main, "run_doctor", fake_doctor)
    monkeypatch.setattr(cli_main, "config_has_mcp_server", lambda *args, **kwargs: False)

    exit_code = cli_main.main(["setup", "codex", "--path", str(workspace)])

    assert exit_code == 0
    assert calls == [
        "grammars",
        ("index", workspace),
        ("mcp", "codex", workspace, "project", False),
        ("instructions", "codex", workspace, None, False),
        ("daemon", workspace),
        ("doctor", workspace, "codex", "project"),
    ]


def test_setup_dry_run_previews_every_step_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dry-run resolves the plan without downloading, indexing, spawning, or writing."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ("python", "rust"))
    monkeypatch.setattr(
        cli_main,
        "install_grammars",
        lambda: pytest.fail("installed grammars"),
    )
    monkeypatch.setattr(cli_main, "index_workspace", lambda path: pytest.fail("indexed"))
    monkeypatch.setattr(
        cli_main,
        "install_mcp_server",
        lambda *args, **kwargs: pytest.fail("installed MCP config"),
    )
    monkeypatch.setattr(
        cli_main,
        "install_instruction_snippet",
        lambda *args, **kwargs: pytest.fail("installed instructions"),
    )
    monkeypatch.setattr(
        cli_main,
        "ensure_watch_daemon",
        lambda path: pytest.fail("started daemon"),
    )
    monkeypatch.setattr(cli_main, "run_doctor", lambda *args, **kwargs: pytest.fail("ran doctor"))

    exit_code = cli_main.main(["setup", "codex", "--path", str(workspace), "--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Synapse setup preview (no changes made)." in captured.out
    assert "Grammars: would install 2 missing parsers" in captured.out
    assert f"MCP config: would install at {workspace / '.codex' / 'config.toml'}" in captured.out
    assert "Watch daemon: would ensure a healthy detached process" in captured.out
    assert not (workspace / ".codex").exists()
    assert not (workspace / "AGENTS.md").exists()


def test_setup_offline_fails_when_grammars_are_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Offline setup fails before any partial installation when parsers are absent."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ("python",))
    monkeypatch.setattr(
        cli_main,
        "install_grammars",
        lambda: pytest.fail("attempted network installation"),
    )
    monkeypatch.setattr(cli_main, "index_workspace", lambda path: pytest.fail("indexed"))

    exit_code = cli_main.main(["setup", "codex", "--path", str(workspace), "--offline"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Rerun without --offline" in captured.err
    assert not (workspace / ".codex").exists()
    assert not (workspace / "AGENTS.md").exists()


def test_setup_no_instructions_keeps_mcp_installation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Instruction opt-out skips only the repository snippet."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _stub_successful_runtime(monkeypatch, workspace)

    exit_code = cli_main.main(["setup", "codex", "--path", str(workspace), "--no-instructions"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert (workspace / ".codex" / "config.toml").exists()
    assert not (workspace / "AGENTS.md").exists()
    assert "Repository instructions: skipped" in captured.out


def test_setup_repeated_run_completes_partial_managed_installation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup preserves user content and converges partial state without duplicate blocks."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = workspace / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text('[profile]\nname = "local"\n', encoding="utf-8")
    instructions_path = workspace / "AGENTS.md"
    instructions_path.write_text("# Existing repository rules\n", encoding="utf-8")
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _stub_successful_runtime(monkeypatch, workspace)

    first = cli_main.main(["setup", "codex", "--path", str(workspace)])
    second = cli_main.main(["setup", "codex", "--path", str(workspace)])

    config = config_path.read_text(encoding="utf-8")
    instructions = instructions_path.read_text(encoding="utf-8")
    assert (first, second) == (0, 0)
    assert config.startswith('[profile]\nname = "local"\n')
    assert config.count(MANAGED_TOML_BEGIN) == 1
    assert config.count(MANAGED_TOML_END) == 1
    assert instructions.startswith("# Existing repository rules\n")
    assert instructions.count(BEGIN_MARKER) == 1
    assert instructions.count(END_MARKER) == 1


def test_setup_explicit_user_scope_is_respected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scope override remains available for users who intentionally share config."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    _stub_successful_runtime(monkeypatch, workspace)

    exit_code = cli_main.main(["setup", "codex", "--path", str(workspace), "--scope", "user"])

    assert exit_code == 0
    assert (home / ".codex" / "config.toml").exists()
    assert not (workspace / ".codex" / "config.toml").exists()


def test_setup_reports_legacy_user_codex_config_without_removing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Project setup gives explicit migration guidance for a legacy global entry."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    install_mcp_server(
        "codex",
        workspace,
        scope="user",
        python_executable="/legacy-python",
    )
    legacy_config = home / ".codex" / "config.toml"
    legacy_text = legacy_config.read_text(encoding="utf-8")
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ())

    exit_code = cli_main.main(["setup", "codex", "--path", str(workspace), "--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"Legacy user-scoped Codex config detected at {legacy_config}" in captured.out
    assert "--scope user --keep-instructions" in captured.out
    assert legacy_config.read_text(encoding="utf-8") == legacy_text
    assert not (workspace / ".codex").exists()


def test_setup_returns_failure_when_doctor_reports_hard_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The orchestrator does not report success when final validation fails."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _stub_successful_runtime(monkeypatch, workspace)
    monkeypatch.setattr(
        cli_main,
        "run_doctor",
        lambda path, *, agent, scope: DoctorReport(
            str(path),
            agent,
            [DoctorCheck("watch", "fail", "daemon unhealthy")],
        ),
    )

    assert cli_main.main(["setup", "codex", "--path", str(workspace)]) == 1


def test_bare_cli_prints_help_and_global_install_hint_without_starting_server(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A bare invocation is safe and guides users to the canonical global install."""
    monkeypatch.setattr(cli_main, "run", lambda **kwargs: pytest.fail("MCP server started"))

    exit_code = cli_main.main([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "usage: synapse" in captured.out
    assert "Get started: synapse install <agent>" in captured.out
