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

from synapse.core.config.ignores import (
    ConfigScope,
    IgnoreMatcher,
    IgnoreProblem,
    IgnoreRule,
    compile_pattern,
    normalize_ignore_entry,
    parse_ignore_text,
    rule_text_for_json_entry,
    validate_ignore_pattern,
)
from synapse.core.workspace import atomic_write_text

_PACKAGE_CONFIG = resources.files("synapse.core") / "default_ignored_directories.json"
_DEFAULT_DEBOUNCE_MS = 250
_DEFAULT_MAX_LATENCY_MS = 2_000
_DEFAULT_BATCH_SIZE = 256
_DEFAULT_STORM_THRESHOLD = 1_000
_DEFAULT_RECONCILE_INTERVAL_S = 600
_DEFAULT_POLL_INTERVAL_S = 5
_EMERGENCY_FALLBACK = frozenset({".git", "__pycache__"})

PROJECT_CONFIG_DIR = ".synapse"
SYNAPSE_IGNORE_FILE = ".synapseignore"
GLOBAL_IGNORE_FILE = "ignore"
BOOTSTRAP_ENV_OPT_OUT = "SYNAPSE_NO_IGNORE_BOOTSTRAP"
AUTO_IGNORE_BOOTSTRAP_KEY = "auto_ignore_bootstrap"

IGNORE_FILE_HEADER = (
    f"# {SYNAPSE_IGNORE_FILE} — gitignore syntax; the last matching rule wins.\n"
    "# Edit freely: Synapse only ever appends to this file.\n"
)


class IgnoreSource(StrEnum):
    """Which file a layer's ignore rules actually came from."""

    IGNORE_FILE = "ignore-file"
    JSON = "json"
    NONE = "none"


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


def synapseignore_path(workspace_root: Path) -> Path:
    """Return the project ignore file path for a workspace."""
    return workspace_root / SYNAPSE_IGNORE_FILE


def global_ignore_path() -> Path:
    """Return the global user ignore file path."""
    return _config_dir() / GLOBAL_IGNORE_FILE


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
class IgnoreLayer:
    """One resolved ignore layer, its rules in order, and the file they came from."""

    scope: ConfigScope
    source: IgnoreSource
    path: Path
    rules: tuple[IgnoreRule, ...]
    problems: tuple[IgnoreProblem, ...] = ()
    shadowed_json_entries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EffectiveConfig:
    """Configuration a workspace actually runs with, as ordered layers of ignore rules.

    There is deliberately no flat set of ignored paths: with negation, whether a path is ignored
    depends on rule order, so any such set would be a claim the rules do not support.
    """

    workspace_path: Path
    project_config_path: Path
    project_config_exists: bool
    synapseignore_path: Path
    global_config_path: Path
    global_ignore_path: Path
    layers: tuple[IgnoreLayer, ...]
    watch: WatchConfig

    @property
    def ignore_rules(self) -> tuple[IgnoreRule, ...]:
        """Return every rule across all layers, in evaluation order."""
        return tuple(rule for layer in self.layers for rule in layer.rules)

    @property
    def ignore_problems(self) -> tuple[IgnoreProblem, ...]:
        """Return every source line that was skipped, across all layers."""
        return tuple(problem for layer in self.layers for problem in layer.problems)

    def layer(self, scope: ConfigScope) -> IgnoreLayer:
        """Return one layer by scope."""
        return next(layer for layer in self.layers if layer.scope is scope)

    def matcher(self) -> IgnoreMatcher:
        """Build the ignore matcher for these effective rules."""
        return IgnoreMatcher.from_rules(self.ignore_rules)


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


def _rules_from_entries(
    entries: Iterable[str],
    *,
    scope: ConfigScope,
    origin: str,
) -> tuple[IgnoreRule, ...]:
    """Compile legacy ignored_directories entries into rules, in a stable order.

    Legacy entries carry no ordering and no negation, so sorting them changes nothing.
    """
    rules = (
        compile_pattern(rule_text_for_json_entry(entry), scope=scope, origin=origin, line=0)
        for entry in sorted(set(entries))
    )
    return tuple(rule for rule in rules if rule is not None)


def auto_ignore_bootstrap_enabled(workspace_root: Path) -> bool:
    """Return whether Synapse may create a .synapseignore during first-run initialization."""
    if os.environ.get(BOOTSTRAP_ENV_OPT_OUT):
        return False
    for path in (project_config_path(workspace_root), config_file_path()):
        try:
            payload = _read_raw_payload(path)
        except (OSError, ValueError):
            continue
        if payload.get(AUTO_IGNORE_BOOTSTRAP_KEY) is False:
            return False
    return True


def load_builtin_ignore_layer() -> IgnoreLayer:
    """Load the package-level default ignore rules."""
    origin = str(_PACKAGE_CONFIG)
    return IgnoreLayer(
        scope=ConfigScope.BUILT_IN,
        source=IgnoreSource.JSON,
        path=Path(origin),
        rules=_rules_from_entries(
            load_default_ignored_directories(), scope=ConfigScope.BUILT_IN, origin=origin
        ),
    )


def _load_ignore_layer(
    *,
    scope: ConfigScope,
    ignore_file: Path,
    json_path: Path,
    json_entries: frozenset[str],
) -> IgnoreLayer:
    """Resolve one layer, preferring its ignore file over its legacy JSON entries."""
    if not ignore_file.exists():
        return IgnoreLayer(
            scope=scope,
            source=IgnoreSource.JSON if json_entries else IgnoreSource.NONE,
            path=json_path if json_entries else ignore_file,
            rules=_rules_from_entries(json_entries, scope=scope, origin=str(json_path)),
        )

    rules, problems = parse_ignore_text(
        ignore_file.read_text(encoding="utf-8"), scope=scope, origin=str(ignore_file)
    )
    if json_entries:
        warnings.warn(
            f"{ignore_file} supersedes ignored_directories in {json_path}; "
            f"those {len(json_entries)} entries are inactive. Run: synapse ignore migrate",
            stacklevel=3,
        )
    return IgnoreLayer(
        scope=scope,
        source=IgnoreSource.IGNORE_FILE,
        path=ignore_file,
        rules=rules,
        problems=problems,
        shadowed_json_entries=tuple(sorted(json_entries)),
    )


def load_global_ignore_layer() -> IgnoreLayer:
    """Resolve the global ignore layer for the current user."""
    return _load_ignore_layer(
        scope=ConfigScope.GLOBAL,
        ignore_file=global_ignore_path(),
        json_path=config_file_path(),
        json_entries=load_user_config().ignored_directories,
    )


def load_project_ignore_layer(workspace_root: Path) -> IgnoreLayer:
    """Resolve the project ignore layer for a workspace."""
    return _load_ignore_layer(
        scope=ConfigScope.PROJECT,
        ignore_file=synapseignore_path(workspace_root),
        json_path=project_config_path(workspace_root),
        json_entries=load_project_config(workspace_root).ignored_directories,
    )


def load_effective_config(workspace_root: Path) -> EffectiveConfig:
    """Resolve built-in, global, and project layers into the config a workspace runs with."""
    project_config = load_project_config(workspace_root)

    return EffectiveConfig(
        workspace_path=workspace_root,
        project_config_path=project_config_path(workspace_root),
        project_config_exists=project_config.exists,
        synapseignore_path=synapseignore_path(workspace_root),
        global_config_path=config_file_path(),
        global_ignore_path=global_ignore_path(),
        layers=(
            load_builtin_ignore_layer(),
            load_global_ignore_layer(),
            load_project_ignore_layer(workspace_root),
        ),
        watch=load_user_config().watch,
    )


def _emergency_rules() -> tuple[IgnoreRule, ...]:
    return _rules_from_entries(
        _EMERGENCY_FALLBACK, scope=ConfigScope.BUILT_IN, origin="emergency fallback"
    )


def active_ignore_matcher(workspace_root: Path) -> IgnoreMatcher:
    """Build a workspace ignore matcher, degrading to defaults when a layer fails to load.

    Rules are concatenated built-in, then global, then project, so a later layer can re-include
    what an earlier one ignored.
    """
    rules: list[IgnoreRule] = []
    try:
        rules.extend(load_builtin_ignore_layer().rules)
    except (OSError, ValueError, RuntimeError) as exc:
        warnings.warn(f"Failed to load package config; using fallback: {exc}", stacklevel=2)
        rules.extend(_emergency_rules())
    try:
        rules.extend(load_global_ignore_layer().rules)
    except (OSError, ValueError) as exc:
        warnings.warn(f"Failed to load user config; using defaults: {exc}", stacklevel=2)
    try:
        rules.extend(load_project_ignore_layer(workspace_root).rules)
    except (OSError, ValueError) as exc:
        warnings.warn(f"Failed to load project config; using defaults: {exc}", stacklevel=2)
    return IgnoreMatcher.from_rules(rules)


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


def _read_text_verbatim(path: Path) -> str:
    """Read a file without translating its line endings."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_ignored_directories(path: Path, entries: Iterable[str]) -> Path:
    """Replace ignored_directories in a config file atomically, preserving unknown keys."""
    payload = _read_raw_payload(path)
    payload["ignored_directories"] = sorted(
        {normalize_ignore_entry(entry, source=str(path)) for entry in entries}
    )
    return atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def write_project_ignored_directories(workspace_root: Path, entries: Iterable[str]) -> Path:
    """Replace the project ignored directory list for a workspace."""
    return _write_ignored_directories(project_config_path(workspace_root), entries)


def write_global_ignored_directories(entries: Iterable[str]) -> Path:
    """Replace the global user ignored directory list."""
    return _write_ignored_directories(config_file_path(), entries)


@dataclass(frozen=True, slots=True)
class IgnoreWriteResult:
    """What one ignore-file mutation actually did."""

    path: Path
    scope: ConfigScope
    created: bool = False
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    negated: tuple[str, ...] = ()
    already_present: tuple[str, ...] = ()
    not_present: tuple[str, ...] = ()
    migrated_from_json: tuple[str, ...] = ()


def _ignore_file_for(workspace_root: Path, scope: ConfigScope) -> tuple[Path, Path]:
    """Return the ignore file and the legacy JSON config file for a scope."""
    if scope is ConfigScope.GLOBAL:
        return global_ignore_path(), config_file_path()
    if scope is ConfigScope.PROJECT:
        return synapseignore_path(workspace_root), project_config_path(workspace_root)
    msg = "Built-in ignore rules ship with Synapse and cannot be written to."
    raise ValueError(msg)


def _drop_json_ignored_directories(path: Path) -> None:
    """Remove the ignored_directories key from a config file, preserving everything else."""
    payload = _read_raw_payload(path)
    if payload.pop("ignored_directories", None) is None:
        return
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _pattern_lines(text: str) -> list[str]:
    """Return the stripped, non-comment, non-blank lines of an ignore file."""
    return [
        stripped
        for line in text.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]


def _line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _adopt_ignore_file(
    ignore_file: Path,
    json_path: Path,
) -> tuple[str, bool, tuple[str, ...]]:
    """Return the ignore file's text, whether it must be created, and any migrated JSON entries.

    Creating the file is also the moment the legacy JSON entries move into it, so a workspace
    never ends up with two live sources.
    """
    if ignore_file.exists():
        return _read_text_verbatim(ignore_file), False, ()

    entries = tuple(sorted(_read_json_ignored_directories(json_path)))
    migrated = [rule_text_for_json_entry(entry) for entry in entries]
    body = "".join(f"{pattern}\n" for pattern in migrated)
    return f"{IGNORE_FILE_HEADER}\n{body}" if migrated else IGNORE_FILE_HEADER, True, entries


def _read_json_ignored_directories(json_path: Path) -> frozenset[str]:
    if not json_path.exists():
        return frozenset()
    return _parse_ignored_directories(json_path.read_text(encoding="utf-8"), source=str(json_path))


def _append_patterns(text: str, patterns: Iterable[str]) -> str:
    """Append patterns to the end of an ignore file, preserving everything already there.

    The end is also the right place semantically: under last-match-wins, a rule the user just
    asked for should beat what came before it.
    """
    ending = _line_ending(text)
    body = text if text.endswith(("\n", "\r\n")) or not text else f"{text}{ending}"
    return body + "".join(f"{pattern}{ending}" for pattern in patterns)


def add_ignore_patterns(
    workspace_root: Path,
    patterns: Iterable[str],
    *,
    scope: ConfigScope = ConfigScope.PROJECT,
) -> IgnoreWriteResult:
    """Add gitignore patterns to a scope's ignore file, creating and adopting it when absent."""
    ignore_file, json_path = _ignore_file_for(workspace_root, scope)
    requested = [validate_ignore_pattern(pattern, source=str(ignore_file)) for pattern in patterns]
    text, created, migrated = _adopt_ignore_file(ignore_file, json_path)

    present = _pattern_lines(text)
    added: list[str] = []
    already_present: list[str] = []
    for pattern in requested:
        if pattern in present or pattern in added:
            already_present.append(pattern)
            continue
        added.append(pattern)

    atomic_write_text(ignore_file, _append_patterns(text, added))
    if created:
        _drop_json_ignored_directories(json_path)

    return IgnoreWriteResult(
        path=ignore_file,
        scope=scope,
        created=created,
        added=tuple(added),
        already_present=tuple(already_present),
        migrated_from_json=migrated,
    )


def remove_ignore_patterns(
    workspace_root: Path,
    patterns: Iterable[str],
    *,
    scope: ConfigScope = ConfigScope.PROJECT,
) -> IgnoreWriteResult:
    """Remove gitignore patterns from a scope's ignore file.

    A pattern this file owns is deleted outright. One inherited from a lower layer cannot be
    deleted, so it is negated instead — that is how a built-in or global rule gets turned off.
    """
    ignore_file, json_path = _ignore_file_for(workspace_root, scope)
    requested = [validate_ignore_pattern(pattern, source=str(ignore_file)) for pattern in patterns]
    text, created, migrated = _adopt_ignore_file(ignore_file, json_path)
    matcher = active_ignore_matcher(workspace_root)

    ending = _line_ending(text)
    lines = text.split(ending)
    removed: list[str] = []
    negated: list[str] = []
    not_present: list[str] = []

    for pattern in requested:
        kept = [line for line in lines if line.strip() != pattern]
        if len(kept) != len(lines):
            lines = kept
            removed.append(pattern)
            continue
        if _is_ignored_by(matcher, pattern):
            negated.append(f"!{pattern}")
            continue
        not_present.append(pattern)

    atomic_write_text(ignore_file, _append_patterns(ending.join(lines), negated))
    if created:
        _drop_json_ignored_directories(json_path)

    return IgnoreWriteResult(
        path=ignore_file,
        scope=scope,
        created=created,
        removed=tuple(removed),
        negated=tuple(negated),
        not_present=tuple(not_present),
        migrated_from_json=migrated,
    )


def _is_ignored_by(matcher: IgnoreMatcher, pattern: str) -> bool:
    """Return whether the path a pattern names is currently ignored by some layer."""
    parts = tuple(segment for segment in pattern.strip("!").split("/") if segment)
    if not parts:
        return False
    return matcher.ignores_relative_path(parts, is_dir=pattern.rstrip().endswith("/"))


def migrate_ignores(
    workspace_root: Path,
    *,
    scope: ConfigScope = ConfigScope.PROJECT,
    force: bool = False,
) -> IgnoreWriteResult:
    """Move a scope's legacy JSON ignored_directories into its ignore file."""
    ignore_file, json_path = _ignore_file_for(workspace_root, scope)
    if ignore_file.exists() and not force:
        msg = f"{ignore_file} already exists. Pass force to overwrite it."
        raise FileExistsError(msg)

    entries = tuple(sorted(_read_json_ignored_directories(json_path)))
    migrated = [rule_text_for_json_entry(entry) for entry in entries]
    body = "".join(f"{pattern}\n" for pattern in migrated)
    atomic_write_text(
        ignore_file, f"{IGNORE_FILE_HEADER}\n{body}" if migrated else IGNORE_FILE_HEADER
    )
    _drop_json_ignored_directories(json_path)

    return IgnoreWriteResult(
        path=ignore_file,
        scope=scope,
        created=True,
        added=tuple(migrated),
        migrated_from_json=entries,
    )


def write_ignore_file(
    workspace_root: Path,
    patterns: Iterable[str],
    *,
    scope: ConfigScope = ConfigScope.PROJECT,
    header: str = IGNORE_FILE_HEADER,
) -> IgnoreWriteResult:
    """Write a scope's ignore file from scratch, replacing whatever was there."""
    ignore_file, json_path = _ignore_file_for(workspace_root, scope)
    created = not ignore_file.exists()
    lines = tuple(patterns)
    atomic_write_text(ignore_file, header + "\n" + "".join(f"{line}\n" for line in lines))

    entries = tuple(sorted(_read_json_ignored_directories(json_path)))
    _drop_json_ignored_directories(json_path)

    return IgnoreWriteResult(
        path=ignore_file,
        scope=scope,
        created=created,
        added=tuple(line for line in lines if line and not line.startswith("#")),
        migrated_from_json=entries,
    )
