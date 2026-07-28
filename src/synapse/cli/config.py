"""CLI commands for managing Synapse configuration."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace, _SubParsersAction
from pathlib import Path

from synapse.core.config import (
    config_file_path,
    load_default_ignored_directories,
    load_effective_config,
    load_project_config,
    load_user_config,
    normalize_ignore_entry,
    project_config_path,
    write_global_ignored_directories,
    write_project_ignored_directories,
)
from synapse.core.workspace import detect_workspace_root, require_workspace_path

_ARGUMENT_SOURCE = "the command arguments"


def _resolve_workspace(args: Namespace) -> Path:
    return detect_workspace_root(require_workspace_path(args.path))


def _scope_entries(scope: str, workspace_root: Path) -> set[str]:
    if scope == "global":
        return set(load_user_config().ignored_directories)
    return set(load_project_config(workspace_root).ignored_directories)


def _scope_path(scope: str, workspace_root: Path) -> Path:
    if scope == "global":
        return config_file_path()
    return project_config_path(workspace_root)


def _write_scope(scope: str, workspace_root: Path, entries: set[str]) -> Path:
    if scope == "global":
        return write_global_ignored_directories(entries)
    return write_project_ignored_directories(workspace_root, entries)


def _requested_entries(args: Namespace) -> list[str]:
    return [normalize_ignore_entry(name, source=_ARGUMENT_SOURCE) for name in args.name]


def _handle_list(args: Namespace) -> int:
    workspace_root = _resolve_workspace(args)
    config = load_effective_config(workspace_root)

    print(f"Ignored directories for {workspace_root}")
    print()
    for entry in config.ignored_directories:
        sources = ", ".join(str(scope) for scope in entry.sources)
        print(f"{entry.value} ({sources})")
    print()
    project_state = "exists" if config.project_config_exists else "missing"
    global_state = "exists" if config.global_config_path.exists() else "missing"
    print(f"project config: {config.project_config_path} ({project_state})")
    print(f"global config: {config.global_config_path} ({global_state})")
    return 0


def _handle_add(args: Namespace) -> int:
    workspace_root = _resolve_workspace(args)
    defaults = load_default_ignored_directories()
    entries = _scope_entries(args.scope, workspace_root)

    added: list[str] = []
    for name in _requested_entries(args):
        if name in defaults or name in entries:
            continue
        entries.add(name)
        added.append(name)

    path = _write_scope(args.scope, workspace_root, entries)
    if added:
        print(f"Added to {path}: {', '.join(added)}")
    else:
        print(f"Nothing added to {path}")
    return 0


def _handle_remove(args: Namespace) -> int:
    workspace_root = _resolve_workspace(args)
    defaults = load_default_ignored_directories()
    requested = _requested_entries(args)
    for name in requested:
        if name in defaults:
            msg = (
                f"Cannot remove built-in ignored directory {name!r}. "
                "Built-ins ship with Synapse and are not removable."
            )
            raise ValueError(msg)

    entries = _scope_entries(args.scope, workspace_root)
    removed: list[str] = []
    for name in requested:
        if name not in entries:
            continue
        entries.discard(name)
        removed.append(name)

    path = _write_scope(args.scope, workspace_root, entries)
    if removed:
        print(f"Removed from {path}: {', '.join(removed)}")
    else:
        print(f"Nothing removed from {path}")
    return 0


def _handle_ignored_dirs(args: Namespace) -> int:
    if getattr(args, "ignored_dirs_command", None) is None:
        return _handle_list(args)
    return int(args.func(args))


def build_config_parser(subparsers: _SubParsersAction[ArgumentParser]) -> None:
    """Register config-management subcommands."""
    ignored_parser = subparsers.add_parser("ignored-dirs", help="Manage ignored directories")
    ignored_subparsers = ignored_parser.add_subparsers(dest="ignored_dirs_command")

    list_parser = ignored_subparsers.add_parser("list", help="List ignored directories")
    list_parser.add_argument("--path", default=".")
    list_parser.set_defaults(func=_handle_list)

    add_parser = ignored_subparsers.add_parser("add", help="Add directory names to ignore")
    add_parser.add_argument("name", nargs="+")
    add_parser.add_argument("--path", default=".")
    add_parser.add_argument("--scope", choices=("project", "global"), default="project")
    add_parser.set_defaults(func=_handle_add)

    remove_parser = ignored_subparsers.add_parser(
        "remove",
        help="Remove directory names from the project or global ignore list",
    )
    remove_parser.add_argument("name", nargs="+")
    remove_parser.add_argument("--path", default=".")
    remove_parser.add_argument("--scope", choices=("project", "global"), default="project")
    remove_parser.set_defaults(func=_handle_remove)

    ignored_parser.set_defaults(func=_handle_ignored_dirs, path=".", scope="project")
