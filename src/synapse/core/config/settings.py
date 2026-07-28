"""User-level and project-level configuration for Synapse indexing behavior."""

from __future__ import annotations

import json
import os
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path

from synapse.core.config.ignores import IgnoreMatcher, normalize_ignore_entry

_PACKAGE_CONFIG = resources.files("synapse.core") / "default_ignored_directories.json"
_DEFAULT_DEBOUNCE_MS = 250
_DEFAULT_MAX_LATENCY_MS = 2_000
_DEFAULT_BATCH_SIZE = 256
_DEFAULT_STORM_THRESHOLD = 1_000
_DEFAULT_RECONCILE_INTERVAL_S = 600
_DEFAULT_POLL_INTERVAL_S = 5
_EMERGENCY_FALLBACK = frozenset({".git", "__pycache__"})

PROJECT_CONFIG_DIR = ".synapse"


class ConfigScope(StrEnum):
    """Layer a configuration value comes from."""

    BUILT_IN = "built-in"
    GLOBAL = "global"
    PROJECT = "project"


def _config_dir() -> Path:
    override = os.environ.get("XDG_CONFIG_HOME")
    if override:
        return Path(override).expanduser().resolve() / "synapse"
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data).expanduser().resolve() / "synapse" / "config"
    return Path.home() / ".config" / "synapse"


def config_file_path() -> Path:
    """Return the global user config file path."""
    return _config_dir() / "config.json"


def project_config_path(workspace_root: Path) -> Path:
    """Return the project config file path for a workspace."""
    return workspace_root / PROJECT_CONFIG_DIR / "config.json"


def _parse_ignored_directories(text: str, *, source: str) -> frozenset[str]:
    """Parse and normalize a JSON config payload containing ignored_directories."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"Invalid JSON in {source}: {exc}"
        raise ValueError(msg) from exc

    if not isinstance(payload, dict):
        msg = f"Config payload must be a JSON object in {source}"
        raise ValueError(msg)

    raw_ignored_directories = payload.get("ignored_directories", [])
    if not isinstance(raw_ignored_directories, list):
        msg = f"ignored_directories must be a JSON array in {source}"
        raise ValueError(msg)

    return frozenset(
        normalize_ignore_entry(raw_name, source=source) for raw_name in raw_ignored_directories
    )


def _positive_int(payload: object, key: str, default: int, *, source: str) -> int:
    if not isinstance(payload, dict) or key not in payload:
        return default
    value = payload[key]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        msg = f"watch.{key} must be a positive integer in {source}"
        raise ValueError(msg)
    return value


def _parse_watch_config(text: str, *, source: str) -> WatchConfig:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"Invalid JSON in {source}: {exc}"
        raise ValueError(msg) from exc

    if not isinstance(payload, dict):
        msg = f"Config payload must be a JSON object in {source}"
        raise ValueError(msg)

    raw_watch = payload.get("watch", {})

    if raw_watch is None:
        raw_watch = {}

    if not isinstance(raw_watch, dict):
        msg = f"watch must be a JSON object in {source}"
        raise ValueError(msg)

    return WatchConfig(
        debounce_ms=_positive_int(raw_watch, "debounce_ms", _DEFAULT_DEBOUNCE_MS, source=source),
        max_latency_ms=_positive_int(
            raw_watch,
            "max_latency_ms",
            _DEFAULT_MAX_LATENCY_MS,
            source=source,
        ),
        batch_size=_positive_int(raw_watch, "batch_size", _DEFAULT_BATCH_SIZE, source=source),
        storm_threshold=_positive_int(
            raw_watch,
            "storm_threshold",
            _DEFAULT_STORM_THRESHOLD,
            source=source,
        ),
        reconcile_interval_s=_positive_int(
            raw_watch,
            "reconcile_interval_s",
            _DEFAULT_RECONCILE_INTERVAL_S,
            source=source,
        ),
        poll_interval_s=_positive_int(
            raw_watch,
            "poll_interval_s",
            _DEFAULT_POLL_INTERVAL_S,
            source=source,
        ),
    )


def load_default_ignored_directories() -> frozenset[str]:
    """Load the package-level default ignored directory list."""
    try:
        text = _PACKAGE_CONFIG.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        msg = f"Missing package config at {_PACKAGE_CONFIG}: {exc}"
        raise RuntimeError(msg) from exc
    return _parse_ignored_directories(text, source=str(_PACKAGE_CONFIG))


@dataclass(frozen=True, slots=True)
class WatchConfig:
    """User-configured watch daemon behavior."""

    debounce_ms: int = _DEFAULT_DEBOUNCE_MS
    max_latency_ms: int = _DEFAULT_MAX_LATENCY_MS
    batch_size: int = _DEFAULT_BATCH_SIZE
    storm_threshold: int = _DEFAULT_STORM_THRESHOLD
    reconcile_interval_s: int = _DEFAULT_RECONCILE_INTERVAL_S
    poll_interval_s: int = _DEFAULT_POLL_INTERVAL_S


@dataclass(frozen=True, slots=True)
class UserConfig:
    """Global user-configured indexing behavior."""

    ignored_directories: frozenset[str]
    watch: WatchConfig = WatchConfig()

    def merged_ignored_directories(self) -> frozenset[str]:
        """Return default and global user-defined ignored directories."""
        return load_default_ignored_directories() | self.ignored_directories


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """Workspace-local indexing behavior read from <workspace>/.synapse/config.json."""

    ignored_directories: frozenset[str]
    exists: bool


@dataclass(frozen=True, slots=True)
class IgnoredDirectoryEntry:
    """One effective ignored directory and every layer that contributes it."""

    value: str
    sources: tuple[ConfigScope, ...]


@dataclass(frozen=True, slots=True)
class EffectiveConfig:
    """Configuration a workspace actually runs with, with per-entry provenance."""

    workspace_path: Path
    project_config_path: Path
    project_config_exists: bool
    global_config_path: Path
    ignored_directories: tuple[IgnoredDirectoryEntry, ...]
    watch: WatchConfig

    def matcher(self) -> IgnoreMatcher:
        """Build the ignore matcher for these effective entries."""
        return IgnoreMatcher.from_entries(entry.value for entry in self.ignored_directories)


def load_user_config() -> UserConfig:
    """Load the global user config file."""
    path = config_file_path()

    if not path.exists():
        return UserConfig(ignored_directories=frozenset())

    text = path.read_text(encoding="utf-8")

    return UserConfig(
        ignored_directories=_parse_ignored_directories(text, source=str(path)),
        watch=_parse_watch_config(text, source=str(path)),
    )


def load_project_config(workspace_root: Path) -> ProjectConfig:
    """Load the project config file for a workspace."""
    path = project_config_path(workspace_root)

    if not path.exists():
        return ProjectConfig(ignored_directories=frozenset(), exists=False)

    text = path.read_text(encoding="utf-8")

    return ProjectConfig(
        ignored_directories=_parse_ignored_directories(text, source=str(path)),
        exists=True,
    )


def _entry_sources(
    value: str,
    defaults: frozenset[str],
    user: frozenset[str],
    project: frozenset[str],
) -> tuple[ConfigScope, ...]:
    layers = (
        (ConfigScope.BUILT_IN, defaults),
        (ConfigScope.GLOBAL, user),
        (ConfigScope.PROJECT, project),
    )
    return tuple(scope for scope, entries in layers if value in entries)


def load_effective_config(workspace_root: Path) -> EffectiveConfig:
    """Merge built-in, global, and project layers into the config a workspace runs with."""
    defaults = load_default_ignored_directories()
    user_config = load_user_config()
    project_config = load_project_config(workspace_root)
    merged = defaults | user_config.ignored_directories | project_config.ignored_directories

    return EffectiveConfig(
        workspace_path=workspace_root,
        project_config_path=project_config_path(workspace_root),
        project_config_exists=project_config.exists,
        global_config_path=config_file_path(),
        ignored_directories=tuple(
            IgnoredDirectoryEntry(
                value=value,
                sources=_entry_sources(
                    value,
                    defaults,
                    user_config.ignored_directories,
                    project_config.ignored_directories,
                ),
            )
            for value in sorted(merged)
        ),
        watch=user_config.watch,
    )


def active_ignore_matcher(workspace_root: Path) -> IgnoreMatcher:
    """Build a workspace ignore matcher, degrading to defaults when a layer fails to load."""
    try:
        entries = set(load_default_ignored_directories())
    except (OSError, ValueError, RuntimeError) as exc:
        warnings.warn(f"Failed to load package config; using fallback: {exc}", stacklevel=2)
        entries = set(_EMERGENCY_FALLBACK)
    try:
        entries |= load_user_config().ignored_directories
    except (OSError, ValueError) as exc:
        warnings.warn(f"Failed to load user config; using defaults: {exc}", stacklevel=2)
    try:
        entries |= load_project_config(workspace_root).ignored_directories
    except (OSError, ValueError) as exc:
        warnings.warn(f"Failed to load project config; using defaults: {exc}", stacklevel=2)
    return IgnoreMatcher.from_entries(entries)


def _read_raw_payload(path: Path) -> dict[str, object]:
    """Read a config file as a raw JSON object, preserving keys Synapse does not own."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"Invalid JSON in {path}: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(payload, dict):
        msg = f"Config payload must be a JSON object in {path}"
        raise ValueError(msg)
    return payload


def _write_ignored_directories(path: Path, entries: Iterable[str]) -> Path:
    """Replace ignored_directories in a config file atomically, preserving unknown keys."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _read_raw_payload(path)
    payload["ignored_directories"] = sorted(
        {normalize_ignore_entry(entry, source=str(path)) for entry in entries}
    )
    temporary_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


def write_project_ignored_directories(workspace_root: Path, entries: Iterable[str]) -> Path:
    """Replace the project ignored directory list for a workspace."""
    return _write_ignored_directories(project_config_path(workspace_root), entries)


def write_global_ignored_directories(entries: Iterable[str]) -> Path:
    """Replace the global user ignored directory list."""
    return _write_ignored_directories(config_file_path(), entries)
