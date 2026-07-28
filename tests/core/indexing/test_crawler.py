"""Tests for workspace crawling and hashing."""

import hashlib
import json
from pathlib import Path

import pytest

from synapse.core.config import (
    config_file_path,
    project_config_path,
    write_global_ignored_directories,
    write_project_ignored_directories,
)
from synapse.core.indexing.crawler import hash_file, iter_source_files


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


def test_iter_source_files_anchors_relative_path_entries_to_the_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A multi-segment entry prunes only the matching path under the workspace root."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    write_project_ignored_directories(tmp_path, {"src/generated"})
    nested_generated = tmp_path / "pkg" / "src" / "generated"
    nested_generated.mkdir(parents=True)
    (tmp_path / "src" / "generated").mkdir(parents=True)
    (tmp_path / "src" / "generated" / "skip.py").write_text("print('no')\n", encoding="utf-8")
    keep_file = nested_generated / "keep.py"
    keep_file.write_text("print('ok')\n", encoding="utf-8")

    assert list(iter_source_files(tmp_path)) == [keep_file]


def test_iter_source_files_anchors_leading_slash_entries_to_the_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'/out' prunes the top-level directory while nested ones stay indexed."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    write_project_ignored_directories(tmp_path, {"/out"})
    (tmp_path / "out").mkdir()
    nested_out = tmp_path / "pkg" / "out"
    nested_out.mkdir(parents=True)
    (tmp_path / "out" / "skip.py").write_text("print('no')\n", encoding="utf-8")
    keep_file = nested_out / "keep.py"
    keep_file.write_text("print('ok')\n", encoding="utf-8")

    assert list(iter_source_files(tmp_path)) == [keep_file]


def test_iter_source_files_unions_project_and_global_layers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project entries add to global ones rather than replacing them."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    write_global_ignored_directories({"cache"})
    write_project_ignored_directories(tmp_path, {"generated"})
    for name in ("cache", "generated", "pkg"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "file.py").write_text("print('x')\n", encoding="utf-8")

    assert list(iter_source_files(tmp_path)) == [tmp_path / "pkg" / "file.py"]


def test_iter_source_files_warns_and_uses_defaults_when_project_config_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken project config degrades to defaults instead of failing the crawl."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    project_path = project_config_path(tmp_path)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    project_path.write_text("{invalid}\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / "pkg").mkdir()
    (tmp_path / ".git" / "hidden.py").write_text("print('no')\n", encoding="utf-8")
    keep_file = tmp_path / "pkg" / "keep.py"
    keep_file.write_text("print('ok')\n", encoding="utf-8")

    with pytest.warns(UserWarning, match="Failed to load project config"):
        files = list(iter_source_files(tmp_path))

    assert files == [keep_file]


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
    from synapse.core.config import settings as config_module

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


def test_iter_source_files_does_not_follow_directory_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symlinked directories (including cycles) are never traversed."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    package_dir = tmp_path / "pkg"
    package_dir.mkdir()
    keep_file = package_dir / "keep.py"
    keep_file.write_text("print('ok')\n", encoding="utf-8")
    try:
        (package_dir / "loop").symlink_to(tmp_path, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    files = list(iter_source_files(tmp_path))

    assert files == [keep_file]


def test_iter_source_files_yields_dangling_file_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dangling file symlinks are yielded; indexing handles the read failure."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    try:
        (tmp_path / "dangling.py").symlink_to(tmp_path / "missing-target.py")
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")

    files = list(iter_source_files(tmp_path))

    assert [path.name for path in files] == ["dangling.py"]
