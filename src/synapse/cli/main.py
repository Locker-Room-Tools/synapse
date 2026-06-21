"""Command-line entry point for Synapse."""

import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from pathlib import Path

from synapse.cli.adapters import (
    adapter_choices,
    get_adapter,
    install_instruction_snippet,
    render_mcp_config,
)
from synapse.cli.doctor import format_report, has_failures, report_to_json, run_doctor
from synapse.core.indexing import IndexStats, index_workspace
from synapse.core.workspace import db_path, logs_dir, metadata_path, normalize_workspace_path
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
        print(
            f"  synapse mcp install {adapter.id} --workspace {workspace_root} "
            "--output <agent-config-path>"
        )
        print(f"  synapse doctor --path {workspace_root} --agent {adapter.id}")
    else:
        print(f"  synapse doctor --path {workspace_root}")
    return 0


def _handle_serve(args: Namespace) -> int:
    run(workspace_path=args.workspace)
    return 0


def _handle_mcp_install(args: Namespace) -> int:
    get_adapter(args.client)
    workspace_root = normalize_workspace_path(args.workspace)
    content = render_mcp_config(workspace_root, agent_id=args.client)
    if args.output is not None:
        output_path = Path(args.output).expanduser().resolve()
        if output_path.exists() and not args.force:
            msg = f"{output_path} already exists; use --force to replace it."
            raise FileExistsError(msg)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        print(f"Wrote MCP config template to {output_path}")
        return 0
    print(content)
    return 0


def _handle_doctor(args: Namespace) -> int:
    report = run_doctor(args.path, agent=args.agent)
    if args.json:
        print(report_to_json(report), end="")
    else:
        print(format_report(report), end="")
    return 1 if has_failures(report) else 0


def build_parser() -> ArgumentParser:
    """Build the Synapse CLI parser."""
    parser = ArgumentParser(prog="synapse")
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

    serve_parser = subparsers.add_parser("serve", help="Run the Synapse MCP server")
    serve_parser.add_argument("--workspace", default=".")
    serve_parser.set_defaults(func=_handle_serve)

    mcp_parser = subparsers.add_parser("mcp", help="MCP client helpers")
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command", required=True)
    install_parser = mcp_subparsers.add_parser("install", help="Print or write an MCP config")
    install_parser.add_argument("client", choices=adapter_choices())
    install_parser.add_argument("--workspace", default=".")
    install_parser.add_argument("--output")
    install_parser.add_argument("--force", action="store_true")
    install_parser.set_defaults(func=_handle_mcp_install)

    doctor_parser = subparsers.add_parser("doctor", help="Validate Synapse through MCP")
    doctor_parser.add_argument("--path", default=".")
    doctor_parser.add_argument("--agent", choices=adapter_choices())
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(func=_handle_doctor)

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
    except (FileExistsError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
