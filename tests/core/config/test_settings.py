"""Tests for user-level configuration helpers."""

import json
from pathlib import Path

import pytest

from synapse.core.config import (
    ConfigScope,
    active_ignore_matcher,
    config_file_path,
    load_default_ignored_directories,
    load_effective_config,
    load_project_config,
    load_user_config,
    project_config_path,
    write_global_ignored_directories,
    write_project_ignored_directories,
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
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))

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


@pytest.mark.parametrize("entry", ["../escape", "C:\\projects", "*.py"])
def test_load_user_config_rejects_unsupported_ignore_entries(
    entry: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escaping, absolute, and glob entries are rejected; relative paths are not."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ignored_directories": [entry]}), encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid ignored directory"):
        load_user_config()


def test_load_user_config_normalizes_relative_path_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace-relative and root-anchored entries are accepted and canonicalized."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ignored_directories": ["./src/generated/", "/build"]}),
        encoding="utf-8",
    )

    assert load_user_config().ignored_directories == frozenset({"src/generated", "/build"})


def test_project_config_path_is_workspace_local(tmp_path: Path) -> None:
    """Project config lives inside the workspace, and readers never create it."""
    assert project_config_path(tmp_path) == tmp_path / ".synapse" / "config.json"
    assert not (tmp_path / ".synapse").exists()


def test_load_project_config_returns_empty_when_missing(tmp_path: Path) -> None:
    """A workspace without a project config reports an empty, non-existent layer."""
    config = load_project_config(tmp_path)

    assert config.ignored_directories == frozenset()
    assert config.exists is False


def test_load_effective_config_reports_every_contributing_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each effective entry names the layers it comes from, not a single winner."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    write_global_ignored_directories({"generated", "global-only"})
    write_project_ignored_directories(workspace_root, {"generated", "src/vendor"})

    config = load_effective_config(workspace_root)
    sources = {entry.value: entry.sources for entry in config.ignored_directories}

    assert sources[".git"] == (ConfigScope.BUILT_IN,)
    assert sources["global-only"] == (ConfigScope.GLOBAL,)
    assert sources["src/vendor"] == (ConfigScope.PROJECT,)
    assert sources["generated"] == (ConfigScope.GLOBAL, ConfigScope.PROJECT)
    assert config.project_config_exists is True


def test_load_effective_config_matcher_covers_all_layers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The matcher built from an effective config honors every layer at once."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    write_global_ignored_directories({"cache"})
    write_project_ignored_directories(workspace_root, {"src/generated"})

    matcher = load_effective_config(workspace_root).matcher()

    assert matcher.ignores_child((), ".git")
    assert matcher.ignores_child(("pkg",), "cache")
    assert matcher.ignores_child(("src",), "generated")
    assert not matcher.ignores_child(("pkg", "src"), "generated")


def test_write_ignored_directories_preserves_unowned_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rewrites keep watch tunables and keys Synapse does not own."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"watch": {"poll_interval_s": 2}, "custom": {"kept": True}}),
        encoding="utf-8",
    )

    write_global_ignored_directories({"generated"})

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "custom": {"kept": True},
        "ignored_directories": ["generated"],
        "watch": {"poll_interval_s": 2},
    }


def test_write_ignored_directories_is_atomic_and_repeatable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writes replace the file in one step and leave no temporary files behind."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    path = write_global_ignored_directories({"generated"})
    first = path.read_bytes()
    write_global_ignored_directories({"generated"})

    assert path.read_bytes() == first
    assert first.endswith(b"\n")
    assert list(path.parent.glob("*.tmp")) == []


def test_write_ignored_directories_normalizes_before_persisting(tmp_path: Path) -> None:
    """Entries are canonicalized on the way to disk, so the file never holds raw input."""
    path = write_project_ignored_directories(tmp_path, {"./src/generated/"})

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["ignored_directories"] == ["src/generated"]


def test_load_project_config_rejects_invalid_json(tmp_path: Path) -> None:
    """A malformed project config names its own path in the error."""
    path = project_config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{invalid}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"Invalid JSON in .*\.synapse"):
        load_project_config(tmp_path)


def test_active_ignore_matcher_warns_and_degrades_on_bad_project_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken project layer warns and falls back instead of breaking indexing."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = project_config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{invalid}\n", encoding="utf-8")

    with pytest.warns(UserWarning, match="Failed to load project config"):
        matcher = active_ignore_matcher(tmp_path)

    assert matcher.ignores_child((), ".git")


def test_write_ignored_directories_refuses_to_clobber_a_broken_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed config is reported, not silently discarded on the next write."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{invalid}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSON"):
        write_global_ignored_directories({"generated"})

    assert path.read_text(encoding="utf-8") == "{invalid}\n"


def test_write_ignored_directories_rejects_non_object_payloads(tmp_path: Path) -> None:
    """A config file holding a JSON array is rejected rather than overwritten."""
    path = project_config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a JSON object"):
        write_project_ignored_directories(tmp_path, {"generated"})
