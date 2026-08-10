"""Tests for the deprecated `synapse config ignored-dirs` aliases."""

from pathlib import Path

import pytest

from synapse.cli import main as cli_main
from synapse.core.config import global_ignore_path, project_config_path, synapseignore_path


def test_config_ignored_dirs_list_still_works_and_warns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The alias keeps listing rules and points at its replacement on stderr."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    exit_code = cli_main.main(["config", "ignored-dirs", "list", "--path", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "built-in" in captured.out
    assert "deprecated; use `synapse ignore list`" in captured.err


def test_config_ignored_dirs_add_writes_the_ignore_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The alias writes wherever the new command writes, not to the legacy JSON."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    exit_code = cli_main.main(
        ["config", "ignored-dirs", "add", "generated/", "src/vendor/", "--path", str(tmp_path)],
    )

    captured = capsys.readouterr()
    text = synapseignore_path(tmp_path).read_text(encoding="utf-8")
    assert exit_code == 0
    assert "generated/" in text
    assert "src/vendor/" in text
    assert not project_config_path(tmp_path).exists()
    assert "deprecated" in captured.err


def test_config_ignored_dirs_add_honors_global_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--scope global still targets the user layer, now its ignore file."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    exit_code = cli_main.main(
        [
            "config",
            "ignored-dirs",
            "add",
            "generated/",
            "--scope",
            "global",
            "--path",
            str(tmp_path),
        ],
    )

    assert exit_code == 0
    assert "generated/" in global_ignore_path().read_text(encoding="utf-8")
    assert not synapseignore_path(tmp_path).exists()


def test_config_ignored_dirs_remove_no_longer_rejects_built_ins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Removing a built-in used to exit 2; negation now makes it a normal success."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    exit_code = cli_main.main(
        ["config", "ignored-dirs", "remove", "node_modules/", "--path", str(tmp_path)],
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "negated: !node_modules/" in captured.out


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
    assert not synapseignore_path(tmp_path).exists()
