"""Command-line entry point for Synapse."""

import json
import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from pathlib import Path

from synapse import __version__
from synapse.cli.adapters import (
    adapter_choices,
    get_adapter,
    install_global_instruction,
    install_global_skill,
    install_instruction_snippet,
    remove_global_instruction,
    remove_global_skill,
    remove_instruction_snippet,
    resolve_instruction_path,
)
from synapse.cli.config import build_config_parser
from synapse.cli.doctor import format_report, has_failures, report_to_json, run_doctor
from synapse.cli.grammars import LanguagePackError, install_grammars, missing_grammars
from synapse.cli.installer import (
    config_has_mcp_server,
    install_mcp_server,
    resolve_config_path,
    standalone_mcp_config,
    uninstall_global_mcp_server,
    uninstall_mcp_server,
)
from synapse.core.grammars import GrammarNotInstalledError
from synapse.core.indexing import IndexStats, index_workspace
from synapse.core.lifecycle import (
    WorkspaceNotReadyError,
    ensure_workspace,
    workspace_status_payload,
)
from synapse.core.watch.daemon import (
    WatchDaemonError,
    ensure_watch_daemon,
    wait_for_watch_to_stop,
)
from synapse.core.watch.state import watch_status_payload
from synapse.core.watch.supervisor import request_watch_stop, run_watch_foreground
from synapse.core.workspace import (
    db_path,
    detect_workspace_root,
    logs_dir,
    metadata_path,
    normalize_workspace_path,
    require_workspace_path,
)
from synapse.mcp.server import run


def _detect_workspace_root(start: Path) -> Path:
    return detect_workspace_root(start)


def _print_index_summary(stats: IndexStats) -> None:
    print("Synapse index updated.")
    print()
    print(f"Workspace: {stats.workspace_path}")
    print(f"Indexed files: {stats.indexed_files}")
    print(f"Skipped files: {stats.skipped_files}")
    print(f"Removed files: {stats.removed_files}")
    if stats.failed_files:
        print(f"Failed files: {stats.failed_files}")
    print(f"Stored files: {stats.total_files}")
    print(f"Stored symbols: {stats.total_symbols}")
    languages = ", ".join(stats.languages) if stats.languages else "none"
    print(f"Languages: {languages}")
    if stats.indexed_files == 0 and stats.skipped_files > 0:
        print()
        print("No files changed; reused the existing index. Use --force to rebuild it.")


def _handle_index(args: Namespace) -> int:
    stats = index_workspace(args.path, force=args.force)
    _print_index_summary(stats)
    return 0


def _handle_grammars_install(_args: Namespace) -> int:
    print("Preparing tree-sitter grammars...")
    grammar_names = install_grammars()
    print(f"Installed and verified {len(grammar_names)} grammars.")
    return 0


def _handle_install(args: Namespace) -> int:
    adapter = get_adapter(args.agent)
    grammar_names = missing_grammars()
    if grammar_names and args.offline:
        msg = (
            f"{len(grammar_names)} supported tree-sitter grammars are missing. "
            "Rerun without --offline or run 'synapse grammars install'."
        )
        raise ValueError(msg)
    anchor = Path.cwd()
    config_preview = install_mcp_server(
        args.agent,
        anchor,
        scope="user",
        force=args.force,
        dry_run=True,
        portable=True,
    )
    instruction_preview = install_global_instruction(
        args.agent,
        force=True,
        dry_run=True,
    )
    skill_preview = None
    if not args.no_skill:
        skill_preview = install_global_skill(
            args.agent,
            force=args.force,
            dry_run=True,
        )
    if args.dry_run:
        config_result = config_preview
        instruction_result = instruction_preview
        skill_result = skill_preview
    else:
        if grammar_names:
            print(f"Installing {len(grammar_names)} missing tree-sitter grammars...")
            install_grammars()
        config_result = install_mcp_server(
            args.agent,
            anchor,
            scope="user",
            force=args.force,
            portable=True,
        )
        instruction_result = install_global_instruction(args.agent, force=True)
        skill_result = (
            None
            if args.no_skill
            else install_global_skill(args.agent, force=args.force)
        )

    heading = "Synapse global install preview." if args.dry_run else "Synapse installed globally."
    print(heading)
    print()
    if args.dry_run:
        grammar_status = (
            f"would install {len(grammar_names)} missing parsers"
            if grammar_names
            else "local cache is ready"
        )
        print(f"Grammars: {grammar_status}")
    else:
        print("Grammars: local cache is ready")
    print(f"MCP config: {config_result.path} ({config_result.action})")
    print(f"Global instructions: {instruction_result.path} ({instruction_result.status})")
    if skill_result is None:
        print("Global skill: skipped")
    else:
        print(f"Global skill: {skill_result.path} ({skill_result.status})")
    if not args.dry_run:
        print()
        print(f"Restart {adapter.display_name} once to load Synapse.")
    _print_project_override_warning(args.agent)
    return 0


def _print_project_override_warning(agent: str) -> None:
    workspace_root = _detect_workspace_root(Path.cwd())
    if not config_has_mcp_server(agent, workspace_root, scope="project"):
        return
    project_config = resolve_config_path(get_adapter(agent), workspace_root, "project")
    print()
    print(f"Project-scoped Synapse config detected at {project_config}.")
    print("It remains unchanged and takes precedence over the global integration.")
    print(
        "After verifying the global install, remove the project setup with: "
        f"synapse uninstall {agent} --path {workspace_root} --scope project"
    )


def _handle_init(args: Namespace) -> int:
    workspace_root = _detect_workspace_root(require_workspace_path(args.path))
    grammar_names = missing_grammars()
    if grammar_names and args.offline:
        msg = (
            f"{len(grammar_names)} supported tree-sitter grammars are missing. "
            "Rerun without --offline or run 'synapse grammars install'."
        )
        raise ValueError(msg)
    if args.dry_run:
        current = workspace_status_payload(workspace_root)
        print("Synapse workspace initialization preview (no changes made).")
        print()
        print(f"Workspace: {workspace_root}")
        print(f"Current state: {current['state']}")
        if grammar_names:
            print(f"Grammars: would install {len(grammar_names)} missing parsers")
        else:
            print("Grammars: local cache is ready")
        print("Index and daemon: would ensure ready")
        return 0

    result = ensure_workspace(
        workspace_root,
        offline=args.offline,
        force=args.force,
    )
    payload = result.to_payload()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Synapse workspace {result.action}: {result.workspace_path}")
        print(
            f"Index: {result.index['files']} files, "
            f"{result.index['symbols']} symbols"
        )
        print(f"Watch daemon: running (pid {result.daemon['pid']})")
    return 0


def _format_workspace_status(payload: dict[str, object]) -> str:
    daemon = payload["daemon"]
    assert isinstance(daemon, dict)
    lines = [
        "Synapse workspace status",
        "",
        f"Workspace: {payload['workspace_path']}",
        f"State: {payload['state']}",
        f"Initialized: {payload['initialized']}",
        f"Daemon running: {daemon['running']}",
        f"Daemon degraded: {daemon['degraded']}",
    ]
    return "\n".join(lines) + "\n"


def _handle_status(args: Namespace) -> int:
    workspace_root = _detect_workspace_root(require_workspace_path(args.path))
    payload = workspace_status_payload(workspace_root)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_format_workspace_status(payload), end="")
    return 0


def _print_legacy_codex_config_warning(
    agent: str,
    workspace_root: Path,
    scope: str,
) -> None:
    if agent != "codex" or scope != "project":
        return
    if not config_has_mcp_server("codex", workspace_root, scope="user"):
        return
    legacy_path = resolve_config_path(get_adapter("codex"), workspace_root, "user")
    print()
    print(f"Legacy user-scoped Codex config detected at {legacy_path}; it was not removed.")
    print(
        "After verifying the project config, remove it with: "
        f"synapse uninstall codex --path {workspace_root} --scope user --keep-instructions"
    )


def _print_setup_preview(
    args: Namespace,
    workspace_root: Path,
    grammar_names: tuple[str, ...],
    scope: str,
) -> None:
    adapter = get_adapter(args.agent)
    print("Synapse setup preview (no changes made).")
    print()
    print(f"Workspace: {workspace_root}")
    if grammar_names:
        print(f"Grammars: would install {len(grammar_names)} missing parsers")
    else:
        print("Grammars: local cache is ready")
    print("Index: would initialize or update")
    print(f"MCP config: would install at {resolve_config_path(adapter, workspace_root, scope)}")
    if args.no_instructions:
        print("Instructions: skipped by --no-instructions")
    else:
        instruction_path = resolve_instruction_path(
            args.agent,
            workspace_root,
            output_path=args.instructions_output,
        )
        print(
            "Instructions: would install at "
            f"{instruction_path}"
        )
    print("Watch daemon: would ensure a healthy detached process")
    print("Doctor: would validate the completed installation")
    _print_legacy_codex_config_warning(args.agent, workspace_root, scope)


def _handle_setup(args: Namespace) -> int:
    workspace_root = _detect_workspace_root(require_workspace_path(args.path))
    adapter = get_adapter(args.agent)
    scope = args.scope or adapter.default_scope
    grammar_names = missing_grammars()
    if grammar_names and args.offline:
        msg = (
            f"{len(grammar_names)} supported tree-sitter grammars are missing. "
            "Rerun without --offline or run 'synapse grammars install'."
        )
        raise ValueError(msg)
    if args.dry_run:
        _print_setup_preview(args, workspace_root, grammar_names, scope)
        return 0
    if args.write_instructions:
        print(
            "warning: --write-instructions is deprecated; setup installs instructions by default.",
            file=sys.stderr,
        )

    if grammar_names:
        print(f"Installing {len(grammar_names)} missing tree-sitter grammars...")
        install_grammars()
    else:
        print("Tree-sitter grammar cache is ready.")

    stats = index_workspace(workspace_root)
    config_result = install_mcp_server(
        args.agent,
        workspace_root,
        scope=scope,
        force=args.force,
    )
    instructions_result = None
    if not args.no_instructions:
        instructions_result = install_instruction_snippet(
            args.agent,
            workspace_root,
            output_path=args.instructions_output,
            force=args.force,
        )
    watch_status = ensure_watch_daemon(workspace_root)

    print()
    print("Synapse workspace initialized.")
    print()
    print(f"Workspace: {workspace_root}")
    print(f"Index storage: {db_path(workspace_root)}")
    print(f"Metadata: {metadata_path(workspace_root)}")
    print(f"Logs: {logs_dir(workspace_root)}")
    languages = ", ".join(stats.languages) if stats.languages else "none"
    print(f"Detected languages: {languages}")
    print(f"MCP config: {config_result.path} ({config_result.action})")
    if instructions_result is not None:
        print(
            f"Repository instructions: {instructions_result.path} "
            f"({instructions_result.status})"
        )
    else:
        print("Repository instructions: skipped")
    print(f"Watch daemon: running via {watch_status.backend} (pid {watch_status.pid})")
    _print_legacy_codex_config_warning(args.agent, workspace_root, scope)

    report = run_doctor(workspace_root, agent=args.agent, scope=scope)
    print()
    print(format_report(report), end="")
    if has_failures(report):
        return 1
    print()
    print("Synapse setup complete.")
    return 0


def _handle_serve(args: Namespace) -> int:
    run(workspace_path=args.workspace)
    return 0


def _format_watch_status(payload: dict[str, object]) -> str:
    lines = ["Synapse watch status", "", f"Workspace: {payload['workspace_path']}"]
    lines.append(f"Running: {payload['running']}")
    lines.append(f"Backend: {payload['backend']}")
    lines.append(f"PID: {payload['pid']}")
    lines.append(f"Pending: {payload['pending']}")
    lines.append(f"Last full sweep: {payload['last_full_sweep_ts']}")
    lines.append(f"Staleness seconds: {payload['staleness_seconds']}")
    lines.append(f"Errors: {payload['errors_count']}")
    return "\n".join(lines) + "\n"


def _handle_watch_start(args: Namespace) -> int:
    workspace_root = require_workspace_path(args.workspace)
    if args.foreground:
        run_watch_foreground(
            workspace_root,
            poll_interval_s=args.poll_interval,
            once=args.once,
        )
        return 0
    if args.once:
        print("synapse watch start --once requires --foreground.", file=sys.stderr)
        return 2
    status = ensure_watch_daemon(workspace_root, poll_interval_s=args.poll_interval)
    print(f"Synapse watch daemon is running for {workspace_root} (pid {status.pid}).")
    return 0


def _handle_watch_stop(args: Namespace) -> int:
    workspace_root = normalize_workspace_path(args.workspace)
    status = request_watch_stop(workspace_root)
    if status.running:
        print(f"Synapse watch daemon stop requested for {workspace_root}.")
    else:
        print(f"Synapse watch daemon is not running for {workspace_root}.")
    return 0


def _handle_watch_status(args: Namespace) -> int:
    workspace_root = require_workspace_path(args.workspace)
    payload = watch_status_payload(workspace_root)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_format_watch_status(payload), end="")
    return 0


def _handle_watch_restart(args: Namespace) -> int:
    workspace_root = require_workspace_path(args.workspace)
    request_watch_stop(workspace_root)
    if not wait_for_watch_to_stop(workspace_root):
        print(
            f"Synapse watch daemon did not stop within timeout for {workspace_root}.",
            file=sys.stderr,
        )
        return 2
    status = ensure_watch_daemon(workspace_root, poll_interval_s=args.poll_interval)
    print(f"Synapse watch daemon restarted for {workspace_root} (pid {status.pid}).")
    return 0


def _handle_mcp_install(args: Namespace) -> int:
    get_adapter(args.client)
    workspace_root = normalize_workspace_path(args.workspace)
    content = standalone_mcp_config(args.client, workspace_root)
    if args.print_config:
        print(content, end="")
        return 0
    if args.output is not None:
        output_path = Path(args.output).expanduser().resolve()
        if output_path.exists() and not args.force:
            msg = f"{output_path} already exists; use --force to replace it."
            raise FileExistsError(msg)
        if args.dry_run:
            print(f"Would write MCP config template to {output_path}")
            print()
            print(content, end="")
            return 0
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        print(f"Wrote MCP config template to {output_path}")
        return 0
    result = install_mcp_server(
        args.client,
        workspace_root,
        scope=args.scope,
        force=args.force,
        dry_run=args.dry_run,
    )
    print(f"MCP config {result.action}: {result.path}")
    if args.dry_run:
        print()
        print(result.content_preview, end="")
    return 0


def _handle_uninstall(args: Namespace) -> int:
    get_adapter(args.agent)
    workspace_root = _detect_workspace_root(normalize_workspace_path(args.path))
    if args.global_scope:
        printed = False
        if not args.keep_config:
            config_result = uninstall_global_mcp_server(
                args.agent,
                workspace_root,
                dry_run=args.dry_run,
            )
            print(f"Global MCP config {config_result.action}: {config_result.path}")
            printed = True
        if not args.keep_instructions:
            instructions_result = remove_global_instruction(
                args.agent,
                dry_run=args.dry_run,
            )
            print(
                f"Global instructions {instructions_result.status}: "
                f"{instructions_result.path}"
            )
            printed = True
        if not args.keep_skill:
            skill_result = remove_global_skill(args.agent, dry_run=args.dry_run)
            print(f"Global skill {skill_result.status}: {skill_result.path}")
            printed = True
        if not printed:
            print("Nothing selected for uninstall.")
        return 0

    printed = False
    if not args.keep_config:
        config_result = uninstall_mcp_server(
            args.agent,
            workspace_root,
            scope=args.scope,
            dry_run=args.dry_run,
        )
        print(f"MCP config {config_result.action}: {config_result.path}")
        printed = True
        if args.dry_run and config_result.content_preview:
            print()
            print(config_result.content_preview, end="")
    if not args.keep_instructions:
        instructions_result = remove_instruction_snippet(
            args.agent,
            workspace_root,
            output_path=args.instructions_output,
            dry_run=args.dry_run,
        )
        print(f"Instructions {instructions_result.status}: {instructions_result.path}")
        printed = True
    if not printed:
        print("Nothing selected for uninstall.")
    return 0


def _handle_doctor(args: Namespace) -> int:
    report = run_doctor(args.path, agent=args.agent, scope=args.scope)
    if args.json:
        print(report_to_json(report), end="")
    else:
        print(format_report(report), end="")
    return 1 if has_failures(report) else 0


def build_parser() -> ArgumentParser:
    """Build the Synapse CLI parser."""
    parser = ArgumentParser(prog="synapse")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    index_parser = subparsers.add_parser("index", help="Index a workspace")
    index_parser.add_argument("path", nargs="?", default=".")
    index_parser.add_argument("--force", action="store_true", help="Reparse unchanged files")
    index_parser.set_defaults(func=_handle_index)

    install_parser = subparsers.add_parser(
        "install",
        help="Install Synapse globally for an agent",
    )
    install_parser.add_argument("agent", choices=adapter_choices())
    install_parser.add_argument("--dry-run", action="store_true")
    install_parser.add_argument("--offline", action="store_true")
    install_parser.add_argument("--no-skill", action="store_true")
    install_parser.add_argument("--force", action="store_true")
    install_parser.set_defaults(func=_handle_install)

    init_parser = subparsers.add_parser("init", help="Initialize the current workspace")
    init_parser.add_argument("--path", default=".")
    init_parser.add_argument("--dry-run", action="store_true")
    init_parser.add_argument("--offline", action="store_true")
    init_parser.add_argument("--force", action="store_true")
    init_parser.add_argument("--json", action="store_true")
    init_parser.set_defaults(func=_handle_init)

    status_parser = subparsers.add_parser("status", help="Show workspace readiness")
    status_parser.add_argument("--path", default=".")
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(func=_handle_status)

    setup_parser = subparsers.add_parser("setup", help="Initialize Synapse for a workspace")
    setup_parser.add_argument(
        "agent",
        choices=adapter_choices(),
    )
    setup_parser.add_argument("--path", default=".")
    setup_parser.add_argument("--scope", choices=("project", "user"))
    setup_parser.add_argument("--dry-run", action="store_true")
    setup_parser.add_argument("--offline", action="store_true")
    instructions_group = setup_parser.add_mutually_exclusive_group()
    instructions_group.add_argument("--no-instructions", action="store_true")
    instructions_group.add_argument(
        "--write-instructions",
        action="store_true",
        help="Deprecated; instructions are installed by default",
    )
    setup_parser.add_argument("--instructions-output")
    setup_parser.add_argument("--force", action="store_true")
    setup_parser.set_defaults(func=_handle_setup)

    grammars_parser = subparsers.add_parser("grammars", help="Manage tree-sitter grammars")
    grammars_subparsers = grammars_parser.add_subparsers(dest="grammars_command", required=True)
    grammars_install = grammars_subparsers.add_parser(
        "install", help="Download all supported grammars"
    )
    grammars_install.set_defaults(func=_handle_grammars_install)

    uninstall_parser = subparsers.add_parser("uninstall", help="Remove Synapse agent setup")
    uninstall_parser.add_argument("agent", choices=adapter_choices())
    uninstall_parser.add_argument("--path", default=".")
    uninstall_parser.add_argument("--scope", choices=("project", "user"))
    uninstall_parser.add_argument("--instructions-output")
    uninstall_parser.add_argument("--keep-instructions", action="store_true")
    uninstall_parser.add_argument("--keep-config", action="store_true")
    uninstall_parser.add_argument("--keep-skill", action="store_true")
    uninstall_parser.add_argument("--global", dest="global_scope", action="store_true")
    uninstall_parser.add_argument("--dry-run", action="store_true")
    uninstall_parser.set_defaults(func=_handle_uninstall)

    serve_parser = subparsers.add_parser("serve", help="Internal MCP stdio entry point")
    serve_parser.add_argument("--workspace")
    serve_parser.set_defaults(func=_handle_serve)

    watch_parser = subparsers.add_parser("watch", help="Run or inspect the watch daemon")
    watch_subparsers = watch_parser.add_subparsers(dest="watch_command", required=True)
    watch_start = watch_subparsers.add_parser("start", help="Start a workspace watch daemon")
    watch_start.add_argument("--workspace", default=".")
    watch_start.add_argument("--foreground", action="store_true")
    watch_start.add_argument("--poll-interval", type=int)
    watch_start.add_argument("--once", action="store_true", help="Run one polling sweep and exit")
    watch_start.set_defaults(func=_handle_watch_start)

    watch_stop = watch_subparsers.add_parser("stop", help="Stop a workspace watch daemon")
    watch_stop.add_argument("--workspace", default=".")
    watch_stop.set_defaults(func=_handle_watch_stop)

    watch_status = watch_subparsers.add_parser("status", help="Show watch daemon status")
    watch_status.add_argument("--workspace", default=".")
    watch_status.add_argument("--json", action="store_true")
    watch_status.set_defaults(func=_handle_watch_status)

    watch_restart = watch_subparsers.add_parser("restart", help="Restart a workspace watch daemon")
    watch_restart.add_argument("--workspace", default=".")
    watch_restart.add_argument("--poll-interval", type=int)
    watch_restart.set_defaults(func=_handle_watch_restart)

    mcp_parser = subparsers.add_parser("mcp", help="MCP client helpers")
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command", required=True)
    install_parser = mcp_subparsers.add_parser("install", help="Print or write an MCP config")
    install_parser.add_argument("client", choices=adapter_choices())
    install_parser.add_argument("--workspace", default=".")
    install_parser.add_argument("--scope", choices=("project", "user"))
    install_parser.add_argument("--output")
    install_parser.add_argument("--dry-run", action="store_true")
    install_parser.add_argument("--print", dest="print_config", action="store_true")
    install_parser.add_argument("--force", action="store_true")
    install_parser.set_defaults(func=_handle_mcp_install)

    doctor_parser = subparsers.add_parser("doctor", help="Validate Synapse through MCP")
    doctor_parser.add_argument("--path", default=".")
    doctor_parser.add_argument("--agent", choices=adapter_choices())
    doctor_parser.add_argument("--scope", choices=("project", "user"))
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(func=_handle_doctor)

    config_parser = subparsers.add_parser("config", help="Manage Synapse configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    build_config_parser(config_subparsers)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Synapse CLI."""
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if not arguments:
        parser = build_parser()
        parser.print_help()
        print()
        print("Get started: synapse install <agent>")
        return 0
    parser = build_parser()
    namespace = parser.parse_args(arguments)
    func = getattr(namespace, "func", None)
    if func is None:
        parser.print_help()
        return 1
    try:
        return int(func(namespace))
    except (
        FileExistsError,
        GrammarNotInstalledError,
        LanguagePackError,
        ValueError,
        WatchDaemonError,
        WorkspaceNotReadyError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
