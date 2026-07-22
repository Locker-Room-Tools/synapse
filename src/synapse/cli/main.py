"""Command-line entry point for Synapse."""

import json
import subprocess
import sys
import time
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from pathlib import Path

from synapse import __version__
from synapse.cli.adapters import (
    adapter_choices,
    get_adapter,
    install_instruction_snippet,
    remove_instruction_snippet,
)
from synapse.cli.config import build_config_parser
from synapse.cli.doctor import format_report, has_failures, report_to_json, run_doctor
from synapse.cli.grammars import LanguagePackError, install_grammars
from synapse.cli.installer import install_mcp_server, standalone_mcp_config, uninstall_mcp_server
from synapse.core.grammars import GrammarNotInstalledError
from synapse.core.indexing import IndexStats, index_workspace
from synapse.core.watch.state import pid_is_running, read_watch_status, watch_status_payload
from synapse.core.watch.supervisor import request_watch_stop, run_watch_foreground
from synapse.core.workspace import (
    db_path,
    logs_dir,
    metadata_path,
    normalize_workspace_path,
    require_workspace_path,
)
from synapse.mcp.server import run


def _detect_workspace_root(start: Path) -> Path:
    candidate = start.resolve()
    markers = {".git", "pyproject.toml", "package.json", "Cargo.toml", ".sln", ".hg"}
    for path in (candidate, *candidate.parents):
        if any((path / marker).exists() for marker in markers):
            return path
    return candidate


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


def _handle_setup(args: Namespace) -> int:
    workspace_root = _detect_workspace_root(normalize_workspace_path(args.path))
    stats = index_workspace(workspace_root)
    print("Synapse workspace initialized.")
    print()
    print(f"Workspace: {workspace_root}")
    print(f"Index storage: {db_path(workspace_root)}")
    print(f"Metadata: {metadata_path(workspace_root)}")
    print(f"Logs: {logs_dir(workspace_root)}")
    languages = ", ".join(stats.languages) if stats.languages else "none"
    print(f"Detected languages: {languages}")
    if args.write_instructions:
        if args.agent is None:
            msg = "--write-instructions requires an agent."
            raise ValueError(msg)
        result = install_instruction_snippet(
            args.agent,
            workspace_root,
            output_path=args.instructions_output,
            force=args.force,
        )
        print(f"Repository files changed: {result.path} ({result.status})")
    else:
        print("Repository files changed: none")
    print()
    print("Next steps:")
    if args.agent is not None:
        adapter = get_adapter(args.agent)
        print(f"  synapse mcp install {adapter.id} --workspace {workspace_root}")
        print(f"  synapse doctor --path {workspace_root} --agent {adapter.id}")
    else:
        print(f"  synapse doctor --path {workspace_root}")
    return 0


def _handle_serve(args: Namespace) -> int:
    run(workspace_path=args.workspace)
    return 0


def _watch_is_running(path: Path) -> bool:
    status = read_watch_status(path)
    return status.running and pid_is_running(status.pid)


def _start_detached_watch(path: Path, *, poll_interval_s: int | None = None) -> int:
    if _watch_is_running(path):
        status = read_watch_status(path)
        return status.pid or 0
    command = [
        sys.executable,
        "-m",
        "synapse",
        "watch",
        "start",
        "--workspace",
        str(path),
        "--foreground",
    ]
    if poll_interval_s is not None:
        command.extend(["--poll-interval", str(poll_interval_s)])
    log_path = logs_dir(path) / "watch.log"
    with log_path.open("a", encoding="utf-8") as log_handle:
        if sys.platform == "win32":
            process = subprocess.Popen(
                command,
                cwd=str(path),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=log_handle,
                creationflags=0x00000008 | 0x00000200,
            )
        else:
            process = subprocess.Popen(
                command,
                cwd=str(path),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=log_handle,
                start_new_session=True,
            )
    return int(process.pid)


def _wait_for_watch_to_stop(path: Path, *, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _watch_is_running(path):
            return True
        time.sleep(0.05)
    return not _watch_is_running(path)


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
    pid = _start_detached_watch(workspace_root, poll_interval_s=args.poll_interval)
    print(f"Synapse watch daemon started for {workspace_root} (pid {pid}).")
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
    if not _wait_for_watch_to_stop(workspace_root):
        print(
            f"Synapse watch daemon did not stop within timeout for {workspace_root}.",
            file=sys.stderr,
        )
        return 2
    pid = _start_detached_watch(workspace_root, poll_interval_s=args.poll_interval)
    print(f"Synapse watch daemon restarted for {workspace_root} (pid {pid}).")
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

    setup_parser = subparsers.add_parser("setup", help="Initialize Synapse for a workspace")
    setup_parser.add_argument(
        "agent",
        nargs="?",
        choices=adapter_choices(),
    )
    setup_parser.add_argument("--path", default=".")
    setup_parser.add_argument("--write-instructions", action="store_true")
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
    uninstall_parser.add_argument("--dry-run", action="store_true")
    uninstall_parser.set_defaults(func=_handle_uninstall)

    serve_parser = subparsers.add_parser("serve", help="Run the Synapse MCP server")
    serve_parser.add_argument("--workspace", default=".")
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
        run()
        return 0
    parser = build_parser()
    namespace = parser.parse_args(arguments)
    func = getattr(namespace, "func", None)
    if func is None:
        parser.print_help()
        return 1
    try:
        return int(func(namespace))
    except (FileExistsError, GrammarNotInstalledError, LanguagePackError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
