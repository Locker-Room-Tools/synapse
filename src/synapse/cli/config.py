"""Deprecated `synapse config ignored-dirs` aliases for `synapse ignore`.

Kept so existing scripts and already-installed agent instructions keep working. Every handler
delegates to the ignore command; nothing but the deprecation notice lives here.
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace, _SubParsersAction

from synapse.cli.ignore import handle_add, handle_list, handle_remove

_REPLACEMENTS = {
    "list": "synapse ignore list",
    "add": "synapse ignore add",
    "remove": "synapse ignore remove",
}


def _warn(command: str) -> None:
    print(
        f"warning: `synapse config ignored-dirs {command}` is deprecated; "
        f"use `{_REPLACEMENTS[command]}`.",
        file=sys.stderr,
    )


def _handle_list(args: Namespace) -> int:
    _warn("list")
    return handle_list(args)


def _handle_add(args: Namespace) -> int:
    _warn("add")
    return handle_add(args)


def _handle_remove(args: Namespace) -> int:
    _warn("remove")
    return handle_remove(args)


def _handle_ignored_dirs(args: Namespace) -> int:
    if getattr(args, "ignored_dirs_command", None) is None:
        return _handle_list(args)
    return int(args.func(args))


def build_config_parser(subparsers: _SubParsersAction[ArgumentParser]) -> None:
    """Register config-management subcommands."""
    ignored_parser = subparsers.add_parser(
        "ignored-dirs",
        help="Deprecated: use `synapse ignore`",
    )
    ignored_subparsers = ignored_parser.add_subparsers(dest="ignored_dirs_command")

    list_parser = ignored_subparsers.add_parser("list", help="List effective ignore rules")
    list_parser.add_argument("--path", default=".")
    list_parser.set_defaults(func=_handle_list)

    add_parser = ignored_subparsers.add_parser("add", help="Add ignore patterns")
    add_parser.add_argument("pattern", nargs="+")
    add_parser.add_argument("--path", default=".")
    add_parser.add_argument("--scope", choices=("project", "global"), default="project")
    add_parser.set_defaults(func=_handle_add)

    remove_parser = ignored_subparsers.add_parser("remove", help="Remove ignore patterns")
    remove_parser.add_argument("pattern", nargs="+")
    remove_parser.add_argument("--path", default=".")
    remove_parser.add_argument("--scope", choices=("project", "global"), default="project")
    remove_parser.set_defaults(func=_handle_remove)

    ignored_parser.set_defaults(func=_handle_ignored_dirs, path=".", scope="project")
