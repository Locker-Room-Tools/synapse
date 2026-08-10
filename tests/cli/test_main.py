"""Tests for CLI argument parsing and dispatch."""

import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from synapse import __version__
from synapse.cli import doctor as cli_doctor
from synapse.cli import main as cli_main
from synapse.cli.doctor import DoctorReport
from synapse.core.config import global_ignore_path
from synapse.core.indexing import IndexStats
from synapse.mcp.profiles import ToolProfile


def test_version_flag_reports_installed_package_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The top-level version flag reports package metadata and exits successfully."""
    with pytest.raises(SystemExit) as exc_info:
        cli_main.main(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out == f"synapse {__version__}\n"


def test_index_command_dispatches_and_prints_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The index subcommand dispatches to the indexing use case."""
    monkeypatch.setattr(
        cli_main,
        "index_workspace",
        lambda path, *, force=False: IndexStats(str(path), 1, 2, 3, 0, 4, 5, ["python"]),
    )

    exit_code = cli_main.main(["index", "."])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Indexed files: 1" in captured.out
    assert "Stored symbols: 5" in captured.out


def test_index_command_forwards_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The index subcommand can force a full reparse of unchanged files."""
    seen: dict[str, object] = {}

    def fake_index_workspace(path: str, *, force: bool = False) -> IndexStats:
        seen["path"] = path
        seen["force"] = force
        return IndexStats(str(path), 1, 0, 0, 0, 1, 1, ["tsx"])

    monkeypatch.setattr(cli_main, "index_workspace", fake_index_workspace)

    exit_code = cli_main.main(["index", ".", "--force"])

    assert exit_code == 0
    assert seen == {"path": ".", "force": True}


def test_grammars_install_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The grammar installer runs only through its dedicated CLI command."""
    monkeypatch.setattr(cli_main, "install_grammars", lambda: ("python", "rust"))

    exit_code = cli_main.main(["grammars", "install"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Installed and verified 2 grammars." in captured.out


def test_setup_command_prints_workspace_details(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The setup subcommand installs project config and managed instructions by default."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        cli_main,
        "index_workspace",
        lambda path, *, force=False: IndexStats(str(path), 1, 0, 0, 0, 1, 1, ["python"]),
    )
    monkeypatch.setattr(cli_main, "_detect_workspace_root", lambda path: tmp_path)
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ())
    monkeypatch.setattr(
        cli_main,
        "ensure_watch_daemon",
        lambda path: SimpleNamespace(backend="polling", pid=1234),
    )
    monkeypatch.setattr(
        cli_main,
        "run_doctor",
        lambda path, *, agent, scope: DoctorReport(str(path), agent, []),
    )

    exit_code = cli_main.main(["setup", "codex", "--path", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Synapse workspace initialized." in captured.out
    assert f"MCP config: {tmp_path / '.codex' / 'config.toml'} (created)" in captured.out
    assert f"Repository instructions: {tmp_path / 'AGENTS.md'} (created)" in captured.out
    assert "Watch daemon: running via polling (pid 1234)" in captured.out
    assert (tmp_path / "AGENTS.md").exists()


def test_setup_command_can_write_agent_instructions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The legacy instruction flag remains accepted as a deprecated no-op."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        cli_main,
        "index_workspace",
        lambda path, *, force=False: IndexStats(str(path), 1, 0, 0, 0, 1, 1, ["python"]),
    )
    monkeypatch.setattr(cli_main, "_detect_workspace_root", lambda path: tmp_path)
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ())
    monkeypatch.setattr(
        cli_main,
        "ensure_watch_daemon",
        lambda path: SimpleNamespace(backend="polling", pid=1234),
    )
    monkeypatch.setattr(
        cli_main,
        "run_doctor",
        lambda path, *, agent, scope: DoctorReport(str(path), agent, []),
    )

    exit_code = cli_main.main(["setup", "codex", "--path", str(tmp_path), "--write-instructions"])

    captured = capsys.readouterr()
    instructions = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert exit_code == 0
    assert "--write-instructions is deprecated" in captured.err
    assert "synapse doctor --path . --agent codex" in instructions


def test_mcp_install_can_write_codex_toml_template(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The mcp install subcommand materializes a workspace-pinned config."""
    output_path = tmp_path / "codex.toml"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    exit_code = cli_main.main(
        [
            "mcp",
            "install",
            "codex",
            "--workspace",
            str(workspace_root),
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    config = tomllib.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert config["mcp_servers"]["synapse"]["args"][-1] == str(workspace_root)
    assert "Wrote MCP config template" in captured.out


def test_mcp_install_auto_writes_default_project_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without --print or --output, mcp install updates the resolved config path."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    exit_code = cli_main.main(["mcp", "install", "opencode", "--workspace", str(workspace_root)])

    captured = capsys.readouterr()
    config_path = workspace_root / "opencode.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert config["mcp"]["synapse"]["command"][-1] == str(workspace_root)
    assert f"MCP config created: {config_path}" in captured.out


def test_mcp_install_auto_writes_default_project_config_for_codex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codex defaults to project-scope TOML without mutating the user config."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    exit_code = cli_main.main(["mcp", "install", "codex", "--workspace", str(workspace_root)])

    captured = capsys.readouterr()
    config_path = workspace_root / ".codex" / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert config["mcp_servers"]["synapse"]["args"][-1] == str(workspace_root)
    assert f"MCP config created: {config_path}" in captured.out
    assert not (home / ".codex" / "config.toml").exists()


def test_mcp_install_print_keeps_stdout_only_behavior(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--print emits config without writing the resolved path."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    exit_code = cli_main.main(
        ["mcp", "install", "codex", "--workspace", str(workspace_root), "--print"]
    )

    captured = capsys.readouterr()
    config = tomllib.loads(captured.out)
    assert exit_code == 0
    assert config["mcp_servers"]["synapse"]["args"][-1] == str(workspace_root)
    assert not (workspace_root / ".codex" / "config.toml").exists()


def test_mcp_install_dry_run_writes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--dry-run previews the resolved config write."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    exit_code = cli_main.main(
        ["mcp", "install", "opencode", "--workspace", str(workspace_root), "--dry-run"]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "MCP config would-create:" in captured.out
    assert '"synapse"' in captured.out
    assert not (workspace_root / "opencode.json").exists()


def test_mcp_install_writes_opencode_template(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The OpenCode adapter materializes OpenCode's mcp config shape."""
    output_path = tmp_path / "opencode.json"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    exit_code = cli_main.main(
        [
            "mcp",
            "install",
            "opencode",
            "--workspace",
            str(workspace_root),
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    config = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert config["mcp"]["synapse"]["type"] == "local"
    assert config["mcp"]["synapse"]["enabled"] is True
    assert config["mcp"]["synapse"]["command"][-2:] == ["--workspace", str(workspace_root)]
    assert "Wrote MCP config template" in captured.out


def test_mcp_install_refuses_to_overwrite_without_force(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Existing config files are protected unless --force is used."""
    output_path = tmp_path / "codex.json"
    output_path.write_text("existing\n", encoding="utf-8")

    exit_code = cli_main.main(["mcp", "install", "codex", "--output", str(output_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "already exists" in captured.err
    assert output_path.read_text(encoding="utf-8") == "existing\n"


def test_uninstall_removes_managed_config_and_instructions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The uninstall command reverses managed MCP config and instruction writes."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        cli_main,
        "index_workspace",
        lambda path, *, force=False: IndexStats(str(path), 1, 0, 0, 0, 1, 1, ["python"]),
    )
    monkeypatch.setattr(cli_main, "_detect_workspace_root", lambda path: workspace_root)
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ())
    monkeypatch.setattr(
        cli_main,
        "ensure_watch_daemon",
        lambda path: SimpleNamespace(backend="polling", pid=1234),
    )
    monkeypatch.setattr(
        cli_main,
        "run_doctor",
        lambda path, *, agent, scope: DoctorReport(str(path), agent, []),
    )
    assert cli_main.main(["setup", "opencode", "--path", str(workspace_root)]) == 0

    exit_code = cli_main.main(["uninstall", "opencode", "--path", str(workspace_root)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "MCP config removed:" in captured.out
    assert "Instructions removed:" in captured.out
    assert not (workspace_root / "opencode.json").exists()
    assert not (workspace_root / "AGENTS.md").exists()


def test_doctor_validates_mcp_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Doctor indexes a workspace and validates the advertised MCP surface."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "sample.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    async def fake_probe(
        _workspace_root: Path,
        _profile: ToolProfile = ToolProfile.DEFAULT,
    ) -> tuple[list[str], int, str | None]:
        expected = cli_doctor.expected_tools(ToolProfile.DEFAULT)
        return sorted(expected), 1, "Use Synapse first"

    monkeypatch.setattr(cli_doctor, "_probe_mcp", fake_probe)
    monkeypatch.setattr(
        cli_doctor,
        "watch_status_payload",
        lambda path: {"running": True, "degraded": False, "backend": "polling"},
    )

    exit_code = cli_main.main(
        ["doctor", "--path", str(workspace_root), "--agent", "codex", "--json"]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    statuses = {check["name"]: check["status"] for check in report["checks"]}
    assert exit_code == 0
    assert statuses["mcp_tools"] == "ok"
    assert statuses["server_instructions"] == "ok"
    assert statuses["mcp_call"] == "ok"


def test_build_parser_registers_config_subcommands() -> None:
    """The CLI parser exposes the config ignored-dirs subcommand tree."""
    namespace = cli_main.build_parser().parse_args(["config", "ignored-dirs"])

    assert namespace.command == "config"
    assert namespace.config_command == "ignored-dirs"
    assert callable(namespace.func)


def test_watch_status_command_prints_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The watch status subcommand exposes persisted daemon health."""
    monkeypatch.setattr(
        cli_main,
        "watch_status_payload",
        lambda path: {"workspace_path": str(path), "running": False, "backend": "none"},
    )

    exit_code = cli_main.main(["watch", "status", "--workspace", str(tmp_path), "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out)["running"] is False


def test_watch_start_foreground_dispatches_to_supervisor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Foreground watch mode stays in-process and forwards bounded-run flags."""
    seen: dict[str, object] = {}

    def fake_run(path: Path, *, poll_interval_s: int | None = None, once: bool = False) -> object:
        seen["path"] = path
        seen["poll_interval_s"] = poll_interval_s
        seen["once"] = once
        return object()

    monkeypatch.setattr(cli_main, "run_watch_foreground", fake_run)

    exit_code = cli_main.main(
        [
            "watch",
            "start",
            "--workspace",
            str(tmp_path),
            "--foreground",
            "--poll-interval",
            "1",
            "--once",
        ]
    )

    assert exit_code == 0
    assert seen == {"path": tmp_path.resolve(), "poll_interval_s": 1, "once": True}


def test_watch_start_once_requires_foreground(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One-shot smoke runs are intentionally foreground-only."""
    exit_code = cli_main.main(["watch", "start", "--workspace", str(tmp_path), "--once"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "requires --foreground" in captured.err


def test_watch_restart_waits_for_stop_before_starting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Restart performs stop, wait, and detached start in order."""
    calls: list[str] = []

    def fake_stop(path: Path) -> None:
        calls.append("stop")

    def fake_wait(path: Path) -> bool:
        calls.append("wait")
        return True

    def fake_ensure(
        path: Path,
        *,
        poll_interval_s: int | None = None,
    ) -> SimpleNamespace:
        calls.append(f"start:{poll_interval_s}")
        return SimpleNamespace(pid=111)

    monkeypatch.setattr(cli_main, "request_watch_stop", fake_stop)
    monkeypatch.setattr(cli_main, "wait_for_watch_to_stop", fake_wait)
    monkeypatch.setattr(cli_main, "ensure_watch_daemon", fake_ensure)

    exit_code = cli_main.main(
        ["watch", "restart", "--workspace", str(tmp_path), "--poll-interval", "3"]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert calls == ["stop", "wait", "start:3"]
    assert "restarted" in captured.out


def test_watch_restart_timeout_does_not_start_new_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Restart fails safely if the old daemon does not exit."""
    started: list[Path] = []
    monkeypatch.setattr(cli_main, "request_watch_stop", lambda path: None)
    monkeypatch.setattr(cli_main, "wait_for_watch_to_stop", lambda path: False)
    monkeypatch.setattr(
        cli_main,
        "ensure_watch_daemon",
        lambda path, **kwargs: started.append(path),
    )

    exit_code = cli_main.main(["watch", "restart", "--workspace", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert started == []
    assert "did not stop" in captured.err


def test_config_ignored_dirs_preserves_watch_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adopting the global ignore file leaves the rest of the config file alone."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config_path = tmp_path / "xdg" / "synapse" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"watch": {"poll_interval_s": 2}}), encoding="utf-8")

    exit_code = cli_main.main(
        [
            "config",
            "ignored-dirs",
            "add",
            "generated",
            "--scope",
            "global",
            "--path",
            str(tmp_path),
        ],
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["watch"] == {"poll_interval_s": 2}
    assert "generated" in global_ignore_path().read_text(encoding="utf-8")
