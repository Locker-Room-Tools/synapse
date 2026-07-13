"""CLI commands for managing user-level Synapse configuration."""

from __future__ import annotations

import json
import os
from argparse import ArgumentParser, Namespace, _SubParsersAction
from pathlib import Path

from synapse.core.config import (
    config_file_path,
    load_default_ignored_directories,
    load_user_config,
    validate_directory_name,
)


def _read_extra() -> set[str]:
    return set(load_user_config().ignored_directories)


def _read_existing_payload() -> dict[str, object]:
    path = config_file_path()
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


def _write_config(extra: set[str]) -> Path:
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _read_existing_payload()
    payload["ignored_directories"] = sorted(extra)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + os.linesep, encoding="utf-8")
    return path


def _handle_list(_args: Namespace) -> int:
    config = load_user_config()
    defaults = load_default_ignored_directories()
    for name in sorted(defaults | config.ignored_directories):
        source = "user" if name in config.ignored_directories else "built-in"
        print(f"{name} ({source})")
    return 0


def _handle_add(args: Namespace) -> int:
    extra = _read_extra()
    defaults = load_default_ignored_directories()
    for name in args.name:
        if name in defaults:
            continue
        validate_directory_name(name)

    added: list[str] = []
    for name in args.name:
        if name in defaults or name in extra:
            continue
        extra.add(name)
        added.append(name)

    _write_config(extra)
    if added:
        print(f"Added: {', '.join(added)}")
    else:
        print("Nothing added")
    return 0


def _handle_remove(args: Namespace) -> int:
    extra = _read_extra()
    defaults = load_default_ignored_directories()
    for name in args.name:
        if name not in defaults:
            validate_directory_name(name)

    removed: list[str] = []
    for name in args.name:
        if name in defaults or name not in extra:
            continue
        extra.discard(name)
        removed.append(name)

    _write_config(extra)
    if removed:
        print(f"Removed: {', '.join(removed)}")
    else:
        print("Nothing removed")
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
    list_parser.set_defaults(func=_handle_list)

    add_parser = ignored_subparsers.add_parser("add", help="Add directory names to ignore")
    add_parser.add_argument("name", nargs="+")
    add_parser.set_defaults(func=_handle_add)

    remove_parser = ignored_subparsers.add_parser(
        "remove",
        help="Remove directory names from the user ignore list",
    )
    remove_parser.add_argument("name", nargs="+")
    remove_parser.set_defaults(func=_handle_remove)

    ignored_parser.set_defaults(func=_handle_ignored_dirs)
