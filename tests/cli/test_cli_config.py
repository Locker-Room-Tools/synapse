"""Tests for config-related CLI commands."""

import json
from pathlib import Path

import pytest

from synapse.cli import main as cli_main
from synapse.core.config import config_file_path


def test_config_ignored_dirs_list_shows_built_in_and_user_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Listing ignored directories shows both default and user-provided entries."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ignored_directories": ["generated"]}), encoding="utf-8")

    exit_code = cli_main.main(["config", "ignored-dirs", "list"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert ".git (built-in)" in captured.out
    assert "generated (user)" in captured.out


def test_config_ignored_dirs_add_writes_new_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Adding ignored directories persists only user-defined names."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    exit_code = cli_main.main(["config", "ignored-dirs", "add", "generated", "cache"])

    captured = capsys.readouterr()
    payload = json.loads(config_file_path().read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload == {"ignored_directories": ["cache", "generated"]}
    assert "Added: generated, cache" in captured.out


def test_config_ignored_dirs_add_skips_built_in_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Built-in ignored directories are not duplicated into user config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    exit_code = cli_main.main(["config", "ignored-dirs", "add", ".git"])

    captured = capsys.readouterr()
    payload = json.loads(config_file_path().read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload == {"ignored_directories": []}
    assert "Nothing added" in captured.out


def test_config_ignored_dirs_remove_only_removes_user_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Removing entries leaves built-ins untouched and removes user entries."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ignored_directories": ["generated"]}), encoding="utf-8")

    exit_code = cli_main.main(["config", "ignored-dirs", "remove", "generated", ".git"])

    captured = capsys.readouterr()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload == {"ignored_directories": []}
    assert "Removed: generated" in captured.out


def test_config_ignored_dirs_add_rejects_invalid_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invalid directory names fail through the main CLI entry point."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    exit_code = cli_main.main(["config", "ignored-dirs", "add", "bad/name"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Invalid directory name" in captured.err
