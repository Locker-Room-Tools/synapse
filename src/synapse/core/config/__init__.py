"""Layered configuration and the shared ignored-directory matcher."""

from synapse.core.config.ignores import IgnoreMatcher, normalize_ignore_entry
from synapse.core.config.settings import (
    PROJECT_CONFIG_DIR,
    ConfigScope,
    EffectiveConfig,
    IgnoredDirectoryEntry,
    ProjectConfig,
    UserConfig,
    WatchConfig,
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

__all__ = [
    "PROJECT_CONFIG_DIR",
    "ConfigScope",
    "EffectiveConfig",
    "IgnoreMatcher",
    "IgnoredDirectoryEntry",
    "ProjectConfig",
    "UserConfig",
    "WatchConfig",
    "active_ignore_matcher",
    "config_file_path",
    "load_default_ignored_directories",
    "load_effective_config",
    "load_project_config",
    "load_user_config",
    "normalize_ignore_entry",
    "project_config_path",
    "write_global_ignored_directories",
    "write_project_ignored_directories",
]
