"""Tests for workspace crawling and hashing."""

import hashlib
import json
from pathlib import Path

import pytest

from synapse.core.config import config_file_path
from synapse.core.crawler import hash_file, iter_source_files


def test_iter_source_files_skips_ignored_directories(tmp_path: Path) -> None:
    """The crawler yields only supported files outside ignored directories."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "pkg" / "lib.cs").write_text("class Program {}\n", encoding="utf-8")
    (tmp_path / "pkg" / "notes.txt").write_text("ignored\n", encoding="utf-8")
    (tmp_path / ".git" / "hidden.py").write_text("print('nope')\n", encoding="utf-8")
    (tmp_path / "node_modules" / "vendor.py").write_text("print('nope')\n", encoding="utf-8")

    files = list(iter_source_files(tmp_path))

    assert files == [tmp_path / "pkg" / "app.py", tmp_path / "pkg" / "lib.cs"]


def test_hash_file_returns_stable_sha256_digest(tmp_path: Path) -> None:
    """File hashing uses deterministic SHA-256 digests."""
    file_path = tmp_path / "sample.py"
    file_path.write_text("print('synapse')\n", encoding="utf-8")

    assert hash_file(file_path) == hashlib.sha256(file_path.read_bytes()).hexdigest()


def test_iter_source_files_skips_user_configured_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User config entries extend the crawler ignore list."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config_path = config_file_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"ignored_directories": ["generated"]}),
        encoding="utf-8",
    )
    (tmp_path / "generated").mkdir()
    (tmp_path / "pkg").mkdir()
    (tmp_path / "generated" / "skip.py").write_text("print('nope')\n", encoding="utf-8")
    (tmp_path / "pkg" / "keep.py").write_text("print('ok')\n", encoding="utf-8")

    assert list(iter_source_files(tmp_path)) == [tmp_path / "pkg" / "keep.py"]


def test_iter_source_files_reloads_user_config_between_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crawler config changes are picked up on the next indexing pass."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config_path = config_file_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "generated").mkdir()
    (tmp_path / "pkg").mkdir()
    generated_file = tmp_path / "generated" / "skip.py"
    keep_file = tmp_path / "pkg" / "keep.py"
    generated_file.write_text("print('later')\n", encoding="utf-8")
    keep_file.write_text("print('ok')\n", encoding="utf-8")

    assert list(iter_source_files(tmp_path)) == [generated_file, keep_file]

    config_path.write_text(
        json.dumps({"ignored_directories": ["generated"]}),
        encoding="utf-8",
    )

    assert list(iter_source_files(tmp_path)) == [keep_file]


def test_iter_source_files_warns_and_uses_defaults_when_config_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid config falls back to the built-in ignored directory list."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config_path = config_file_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{invalid}\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / "pkg").mkdir()
    hidden_file = tmp_path / ".git" / "hidden.py"
    keep_file = tmp_path / "pkg" / "keep.py"
    hidden_file.write_text("print('nope')\n", encoding="utf-8")
    keep_file.write_text("print('ok')\n", encoding="utf-8")

    with pytest.warns(UserWarning, match="Failed to load user config"):
        files = list(iter_source_files(tmp_path))

    assert files == [keep_file]


def test_iter_source_files_warns_and_uses_fallback_when_package_config_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing package config falls back to a minimal emergency list."""
    from synapse.core import config as config_module

    monkeypatch.setattr(
        config_module,
        "_PACKAGE_CONFIG",
        tmp_path / "nonexistent" / "default_ignored_directories.json",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / ".git").mkdir()
    (tmp_path / "pkg").mkdir()
    hidden_file = tmp_path / ".git" / "hidden.py"
    keep_file = tmp_path / "pkg" / "keep.py"
    hidden_file.write_text("print('nope')\n", encoding="utf-8")
    keep_file.write_text("print('ok')\n", encoding="utf-8")

    with pytest.warns(UserWarning, match="Failed to load package config"):
        files = list(iter_source_files(tmp_path))

    assert files == [keep_file]