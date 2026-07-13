"""User-level configuration for Synapse indexing behavior."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

_PACKAGE_CONFIG = resources.files("synapse.core") / "default_ignored_directories.json"
_DEFAULT_DEBOUNCE_MS = 250
_DEFAULT_MAX_LATENCY_MS = 2_000
_DEFAULT_BATCH_SIZE = 256
_DEFAULT_STORM_THRESHOLD = 1_000
_DEFAULT_RECONCILE_INTERVAL_S = 600
_DEFAULT_POLL_INTERVAL_S = 5


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
    """Return the user config file path."""
    return _config_dir() / "config.json"


def validate_directory_name(name: str) -> None:
    """Validate a single ignored directory name."""
    if not name or "/" in name or "\\" in name or os.path.sep in name or name in {".", ".."}:
        msg = f"Invalid directory name: {name!r}"
        raise ValueError(msg)


def _parse_ignored_directories(text: str, *, source: str) -> frozenset[str]:
    """Parse and validate a JSON config payload containing ignored_directories."""
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

    ignored_directories: set[str] = set()
    for raw_name in raw_ignored_directories:
        if not isinstance(raw_name, str):
            msg = f"All ignored_directories entries must be strings in {source}"
            raise ValueError(msg)
        validate_directory_name(raw_name)
        ignored_directories.add(raw_name)
    return frozenset(ignored_directories)


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
    """User-configured indexing behavior."""

    ignored_directories: frozenset[str]
    watch: WatchConfig = WatchConfig()

    def merged_ignored_directories(self) -> frozenset[str]:
        """Return default and user-defined ignored directories."""
        return load_default_ignored_directories() | self.ignored_directories


def load_user_config() -> UserConfig:
    """Load the current user config file."""
    path = config_file_path()
    if not path.exists():
        return UserConfig(ignored_directories=frozenset())
    text = path.read_text(encoding="utf-8")
    return UserConfig(
        ignored_directories=_parse_ignored_directories(text, source=str(path)),
        watch=_parse_watch_config(text, source=str(path)),
    )