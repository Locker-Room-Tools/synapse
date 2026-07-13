"""Tests for user-level configuration helpers."""

import json
from pathlib import Path

import pytest

from synapse.core.config import (
    config_file_path,
    load_default_ignored_directories,
    load_user_config,
)


def test_config_file_path_uses_xdg_config_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The config file lives under the XDG config root when provided."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    assert config_file_path() == (tmp_path / "xdg" / "synapse" / "config.json").resolve()


def test_config_file_path_defaults_to_home_config_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The config file falls back to ~/.config/synapse/config.json."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home_dir))

    assert config_file_path() == home_dir / ".config" / "synapse" / "config.json"


def test_load_default_ignored_directories_reads_package_config() -> None:
    """The package-level default config contains the expected directory names."""
    defaults = load_default_ignored_directories()

    assert ".git" in defaults
    assert "__pycache__" in defaults
    assert "node_modules" in defaults


def test_load_user_config_returns_empty_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing config files yield an empty user config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    config = load_user_config()

    assert config.ignored_directories == frozenset()
    assert config.merged_ignored_directories() == load_default_ignored_directories()


def test_load_user_config_reads_valid_ignored_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User config entries extend the built-in ignored directory set."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ignored_directories": ["cache", "generated"]}), encoding="utf-8")

    config = load_user_config()

    assert config.ignored_directories == frozenset({"cache", "generated"})
    assert {"cache", "generated", ".git"}.issubset(config.merged_ignored_directories())


def test_load_user_config_reads_watch_tunables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Watch debounce and polling settings are read from user config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"watch": {"debounce_ms": 100, "poll_interval_s": 2}}),
        encoding="utf-8",
    )

    config = load_user_config()

    assert config.watch.debounce_ms == 100
    assert config.watch.poll_interval_s == 2


def test_load_user_config_rejects_invalid_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed JSON is rejected with a clear error."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSON"):
        load_user_config()


def test_load_user_config_rejects_invalid_directory_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignored directory entries must be simple directory names."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ignored_directories": ["bad/name"]}), encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid directory name"):
        load_user_config()