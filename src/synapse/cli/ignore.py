"""CLI commands for managing Synapse ignore rules."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace, _SubParsersAction
from pathlib import Path

from synapse.core.config import (
    ConfigScope,
    EffectiveConfig,
    IgnoreWriteResult,
    add_ignore_patterns,
    load_effective_config,
    migrate_ignores,
    remove_ignore_patterns,
    validate_ignore_pattern,
    write_ignore_file,
)
from synapse.core.config.ignore_presets import (
    PRESETS,
    detect_presets,
    preset_patterns,
    render_preset_file,
)
from synapse.core.workspace import detect_workspace_root, require_workspace_path

_ARGUMENT_SOURCE = "the command arguments"


def _resolve_workspace(args: Namespace) -> Path:
    return detect_workspace_root(require_workspace_path(args.path))


def _scope(args: Namespace) -> ConfigScope:
    return (
        ConfigScope.GLOBAL if getattr(args, "scope", "project") == "global" else ConfigScope.PROJECT
    )


def _requested_patterns(args: Namespace) -> list[str]:
    """Collect explicit patterns and any --preset expansions, in the order given."""
    patterns = [
        validate_ignore_pattern(pattern, source=_ARGUMENT_SOURCE)
        for pattern in getattr(args, "pattern", []) or []
    ]
    for preset_id in getattr(args, "preset", None) or []:
        patterns.extend(preset_patterns(preset_id))
    if not patterns:
        msg = "Provide at least one pattern or --preset."
        raise ValueError(msg)
    return patterns


def _selected_presets(args: Namespace) -> tuple[str, ...]:
    """Return the presets named by flags, falling back to detection."""
    explicit = tuple(preset_id for preset_id in PRESETS if getattr(args, preset_id, False))
    if explicit:
        return explicit
    detected = detect_presets(_resolve_workspace(args))
    if not detected:
        msg = (
            "No ecosystem detected in this workspace. "
            f"Name one explicitly, e.g. --node. Available presets: {', '.join(sorted(PRESETS))}."
        )
        raise ValueError(msg)
    return detected


def _report(result: IgnoreWriteResult) -> None:
    verb = "Created" if result.created else "Updated"
    print(f"{verb} {result.path}")
    for label, values in (
        ("added", result.added),
        ("removed", result.removed),
        ("negated", result.negated),
        ("already present", result.already_present),
        ("not present", result.not_present),
        ("migrated from config.json", result.migrated_from_json),
    ):
        if values:
            print(f"  {label}: {', '.join(values)}")


def render_ignore_rules(config: EffectiveConfig, workspace_root: Path) -> int:
    """Print every effective rule in evaluation order, with its layer, file, and line."""
    project = config.layer(ConfigScope.PROJECT)
    global_layer = config.layer(ConfigScope.GLOBAL)
    print(f"Ignore rules for {workspace_root}")
    print(f"  project: {project.path} ({project.source})")
    print(f"  global:  {global_layer.path} ({global_layer.source})")
    print()

    for rule in config.ignore_rules:
        # A negated rule already starts with '!', so it needs no extra marker.
        location = f"{Path(rule.origin).name}:{rule.line}" if rule.line else Path(rule.origin).name
        print(f"  {rule.scope:<9} {rule.pattern:<28} {location}")

    print()
    print("Last matching rule wins; '!' re-includes. '.git' is always ignored.")
    for problem in config.ignore_problems:
        print(f"Skipped: {problem.origin}:{problem.line} {problem.text!r} ({problem.reason})")
    if project.shadowed_json_entries:
        print(
            f"Shadowed: ignored_directories in {config.project_config_path} "
            "— run: synapse ignore migrate"
        )
    return 0


def handle_list(args: Namespace) -> int:
    workspace_root = _resolve_workspace(args)
    return render_ignore_rules(load_effective_config(workspace_root), workspace_root)


def _handle_init(args: Namespace) -> int:
    workspace_root = _resolve_workspace(args)
    presets = _selected_presets(args)
    header, lines = render_preset_file(presets)

    scope = _scope(args)
    target = load_effective_config(workspace_root).synapseignore_path
    if target.exists() and not args.force:
        msg = (
            f"{target} already exists. Use `synapse ignore add` to extend it, "
            "or pass --force to rewrite it."
        )
        raise FileExistsError(msg)

    if args.dry_run:
        print(f"Would write {target}")
        print()
        print(header + "\n" + "".join(f"{line}\n" for line in lines), end="")
        return 0

    _report(write_ignore_file(workspace_root, lines, scope=scope, header=header))
    return 0


def handle_add(args: Namespace) -> int:
    workspace_root = _resolve_workspace(args)
    _report(add_ignore_patterns(workspace_root, _requested_patterns(args), scope=_scope(args)))
    return 0


def handle_remove(args: Namespace) -> int:
    workspace_root = _resolve_workspace(args)
    _report(remove_ignore_patterns(workspace_root, _requested_patterns(args), scope=_scope(args)))
    return 0


def _handle_migrate(args: Namespace) -> int:
    workspace_root = _resolve_workspace(args)
    _report(migrate_ignores(workspace_root, scope=_scope(args), force=args.force))
    return 0


def _handle_presets(args: Namespace) -> int:
    detected = set(detect_presets(_resolve_workspace(args)))
    for preset in PRESETS.values():
        mark = "*" if preset.id in detected else " "
        markers = ", ".join(preset.markers) if preset.markers else "opt-in only"
        print(f" {mark} {preset.id:<11}{preset.display_name:<20}{markers}")
    print()
    print("* detected in this workspace")
    return 0


def _handle_ignore(args: Namespace) -> int:
    if getattr(args, "ignore_command", None) is None:
        return handle_list(args)
    return int(args.func(args))


def _add_scope_arguments(parser: ArgumentParser) -> None:
    parser.add_argument("--path", default=".")
    parser.add_argument("--scope", choices=("project", "global"), default="project")


def build_ignore_parser(subparsers: _SubParsersAction[ArgumentParser]) -> None:
    """Register the ignore-management subcommands."""
    ignore_parser = subparsers.add_parser("ignore", help="Manage ignore rules")
    ignore_subparsers = ignore_parser.add_subparsers(dest="ignore_command")

    init_parser = ignore_subparsers.add_parser(
        "init",
        help="Create .synapseignore from ecosystem presets",
    )
    init_parser.add_argument("--path", default=".")
    init_parser.add_argument("--scope", choices=("project", "global"), default="project")
    init_parser.add_argument("--auto", action="store_true", help="Detect presets (the default)")
    init_parser.add_argument("--force", action="store_true", help="Rewrite an existing file")
    init_parser.add_argument("--dry-run", action="store_true")
    for preset in PRESETS.values():
        init_parser.add_argument(
            f"--{preset.id}",
            action="store_true",
            help=f"Include the {preset.display_name} template",
        )
    init_parser.set_defaults(func=_handle_init)

    list_parser = ignore_subparsers.add_parser("list", help="List effective ignore rules")
    list_parser.add_argument("--path", default=".")
    list_parser.set_defaults(func=handle_list)

    add_parser = ignore_subparsers.add_parser("add", help="Add ignore patterns")
    add_parser.add_argument("pattern", nargs="*")
    add_parser.add_argument("--preset", action="append", choices=sorted(PRESETS))
    _add_scope_arguments(add_parser)
    add_parser.set_defaults(func=handle_add)

    remove_parser = ignore_subparsers.add_parser(
        "remove",
        help="Remove ignore patterns, negating any inherited from a lower layer",
    )
    remove_parser.add_argument("pattern", nargs="*")
    remove_parser.add_argument("--preset", action="append", choices=sorted(PRESETS))
    _add_scope_arguments(remove_parser)
    remove_parser.set_defaults(func=handle_remove)

    migrate_parser = ignore_subparsers.add_parser(
        "migrate",
        help="Move legacy ignored_directories from config.json into the ignore file",
    )
    _add_scope_arguments(migrate_parser)
    migrate_parser.add_argument("--force", action="store_true")
    migrate_parser.set_defaults(func=_handle_migrate)

    presets_parser = ignore_subparsers.add_parser("presets", help="List available presets")
    presets_parser.add_argument("--path", default=".")
    presets_parser.set_defaults(func=_handle_presets)

    ignore_parser.set_defaults(func=_handle_ignore, path=".", scope="project")


__all__ = [
    "build_ignore_parser",
    "handle_add",
    "handle_list",
    "handle_remove",
    "render_ignore_rules",
]
