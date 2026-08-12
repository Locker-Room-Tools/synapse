"""Tests for agent-independent workspace initialization and status."""

import json
from pathlib import Path

import pytest

from synapse.cli import main as cli_main
from synapse.core.lifecycle import EnsureWorkspaceResult
from synapse.core.provenance import runtime_provenance


def test_init_delegates_to_shared_workspace_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Manual init is the CLI equivalent of the MCP ensure tool."""
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ())

    def fake_ensure(
        path: Path,
        *,
        offline: bool,
        force: bool,
    ) -> EnsureWorkspaceResult:
        seen.update(path=path, offline=offline, force=force)
        return EnsureWorkspaceResult(
            workspace_path=str(path),
            action="initialized",
            initialized=True,
            daemon={"running": True, "degraded": False, "pid": 1234},
            index={"files": 2, "symbols": 4, "languages": ["python"]},
            runtime=runtime_provenance().to_payload(),
        )

    monkeypatch.setattr(cli_main, "ensure_workspace", fake_ensure)

    exit_code = cli_main.main(["init", "--path", str(tmp_path), "--force", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert seen == {"path": tmp_path, "offline": False, "force": True}
    assert payload["action"] == "initialized"
    assert payload["daemon"]["running"] is True
    assert payload["index"] == {"files": 2, "languages": ["python"], "symbols": 4}


def test_init_dry_run_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Previewing initialization does not allocate cache state or start work."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data-root"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ("python",))
    monkeypatch.setattr(
        cli_main,
        "ensure_workspace",
        lambda *args, **kwargs: pytest.fail("initialized workspace"),
    )

    exit_code = cli_main.main(["init", "--path", str(workspace), "--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Current state: uninitialized" in captured.out
    assert "would install 1 missing parsers" in captured.out
    assert not data_root.exists()


def test_init_offline_fails_before_lifecycle_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI performs its offline grammar preflight before ensure."""
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ("python",))
    monkeypatch.setattr(
        cli_main,
        "ensure_workspace",
        lambda *args, **kwargs: pytest.fail("initialized workspace"),
    )

    exit_code = cli_main.main(["init", "--path", str(tmp_path), "--offline"])

    assert exit_code == 2
    assert "Rerun without --offline" in capsys.readouterr().err


def test_status_human_and_json_are_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both status formats inspect a new workspace without creating its data root."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data-root"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))

    human_exit = cli_main.main(["status", "--path", str(workspace)])
    human = capsys.readouterr().out
    json_exit = cli_main.main(["status", "--path", str(workspace), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert (human_exit, json_exit) == (0, 0)
    assert "State: uninitialized" in human
    assert "Initialized: False" in human
    assert payload["workspace_path"] == str(workspace)
    assert payload["state"] == "uninitialized"
    assert payload["initialized"] is False
    assert not data_root.exists()


def test_legacy_project_setup_remains_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Global onboarding does not remove the explicit project-scoped setup command."""
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ())
    monkeypatch.setattr(cli_main, "config_has_mcp_server", lambda *args, **kwargs: False)

    exit_code = cli_main.main(["setup", "codex", "--path", str(tmp_path), "--dry-run"])

    assert exit_code == 0
    assert "Synapse setup preview" in capsys.readouterr().out
    assert not (tmp_path / ".codex").exists()
