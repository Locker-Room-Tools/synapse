"""Tests for config-related CLI commands."""

import json
from pathlib import Path

import pytest

from synapse.cli import main as cli_main
from synapse.core.config import config_file_path, project_config_path


def test_config_ignored_dirs_list_shows_every_contributing_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Listing annotates each entry with the layers that contribute it."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    global_path = config_file_path()
    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text(
        json.dumps({"ignored_directories": ["generated", "global-only"]}),
        encoding="utf-8",
    )
    project_path = project_config_path(tmp_path)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    project_path.write_text(
        json.dumps({"ignored_directories": ["generated", "src/vendor"]}),
        encoding="utf-8",
    )

    exit_code = cli_main.main(["config", "ignored-dirs", "list", "--path", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert ".git (built-in)" in captured.out
    assert "global-only (global)" in captured.out
    assert "src/vendor (project)" in captured.out
    assert "generated (global, project)" in captured.out
    assert "project config:" in captured.out


def test_config_ignored_dirs_add_defaults_to_project_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adding writes the workspace config, leaving the global config untouched."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    exit_code = cli_main.main(
        ["config", "ignored-dirs", "add", "generated", "src/vendor", "--path", str(tmp_path)],
    )

    captured = capsys.readouterr()
    payload = json.loads(project_config_path(tmp_path).read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload == {"ignored_directories": ["generated", "src/vendor"]}
    assert not config_file_path().exists()
    assert str(project_config_path(tmp_path)) in captured.out


def test_config_ignored_dirs_add_honors_global_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--scope global writes the user-level config instead of the workspace one."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

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

    payload = json.loads(config_file_path().read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload == {"ignored_directories": ["generated"]}
    assert not project_config_path(tmp_path).exists()


def test_config_ignored_dirs_add_skips_built_in_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Built-in ignored directories are not duplicated into a writable layer."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    exit_code = cli_main.main(["config", "ignored-dirs", "add", ".git", "--path", str(tmp_path)])

    captured = capsys.readouterr()
    payload = json.loads(project_config_path(tmp_path).read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload == {"ignored_directories": []}
    assert "Nothing added" in captured.out


def test_config_ignored_dirs_remove_drops_project_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Removing clears entries from the selected scope only."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    project_path = project_config_path(tmp_path)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    project_path.write_text(json.dumps({"ignored_directories": ["generated"]}), encoding="utf-8")

    exit_code = cli_main.main(
        ["config", "ignored-dirs", "remove", "generated", "--path", str(tmp_path)],
    )

    captured = capsys.readouterr()
    payload = json.loads(project_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload == {"ignored_directories": []}
    assert "Removed from" in captured.out


def test_config_ignored_dirs_remove_rejects_built_in_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Built-in ignores cannot be removed, and saying so beats a silent no-op."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    exit_code = cli_main.main(["config", "ignored-dirs", "remove", ".git", "--path", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "not removable" in captured.err


def test_config_ignored_dirs_add_rejects_invalid_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Entries escaping the workspace fail through the main CLI entry point."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    exit_code = cli_main.main(
        ["config", "ignored-dirs", "add", "../escape", "--path", str(tmp_path)],
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Invalid ignored directory" in captured.err
    assert not project_config_path(tmp_path).exists()
