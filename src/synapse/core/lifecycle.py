"""Per-workspace initialization and readiness lifecycle."""

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from synapse.core.index import SymbolIndex
from synapse.core.indexing import index_workspace, reference_index_is_stale
from synapse.core.languages.grammar_install import install_grammars, missing_grammars
from synapse.core.provenance import runtime_provenance
from synapse.core.watch.daemon import ensure_watch_daemon, wait_for_watch_to_stop
from synapse.core.watch.state import pid_is_running, read_watch_status, watch_status_payload
from synapse.core.watch.supervisor import request_watch_stop
from synapse.core.workspace import db_path, read_metadata, require_workspace_path


class WorkspaceState(StrEnum):
    """User-facing workspace lifecycle states."""

    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    READY = "ready"
    DEGRADED = "degraded"


class WorkspaceNotReadyError(RuntimeError):
    """Raised when a query is attempted before workspace initialization."""


@dataclass(frozen=True, slots=True)
class EnsureWorkspaceResult:
    """Result of idempotently preparing one workspace."""

    workspace_path: str
    action: str
    initialized: bool
    daemon: dict[str, object]
    index: dict[str, object]
    # Identity of the Synapse serving this call, so a stale globally-installed build
    # is visible rather than mistaken for the checkout under development.
    runtime: dict[str, object]

    def to_payload(self) -> dict[str, object]:
        """Return the MCP/CLI response shape."""
        return asdict(self)


def workspace_status_payload(workspace_path: str | Path) -> dict[str, object]:
    """Return read-only initialization and daemon health for a workspace."""
    root = require_workspace_path(workspace_path)
    initialized = read_metadata(root) is not None
    watch = watch_status_payload(root)
    running = bool(watch["running"])
    degraded = bool(watch["degraded"])
    if not initialized:
        state = WorkspaceState.INITIALIZING if running else WorkspaceState.UNINITIALIZED
    elif running and not degraded:
        state = WorkspaceState.READY
    else:
        state = WorkspaceState.DEGRADED
    return {
        "workspace_path": str(root),
        "state": state.value,
        "initialized": initialized,
        "daemon": watch,
        "runtime": runtime_provenance().to_payload(),
    }


def require_workspace_ready(workspace_path: str | Path) -> Path:
    """Return a ready workspace or direct the agent to the ensure tool."""
    root = require_workspace_path(workspace_path)
    status = workspace_status_payload(root)
    if status["state"] != WorkspaceState.READY:
        msg = (
            f"Synapse workspace is {status['state']}. "
            "Call synapse_ensure_workspace before using query tools."
        )
        raise WorkspaceNotReadyError(msg)
    return root


def ensure_workspace(
    workspace_path: str | Path,
    *,
    offline: bool = False,
    force: bool = False,
) -> EnsureWorkspaceResult:
    """Install parsers, initialize the index, and ensure a healthy daemon."""
    root = require_workspace_path(workspace_path)
    initialized_before = read_metadata(root) is not None
    missing = missing_grammars()

    if missing and offline:
        msg = (
            f"{len(missing)} supported tree-sitter grammars are missing. "
            "Rerun without offline mode or run 'synapse grammars install'."
        )
        raise ValueError(msg)

    watch_before = read_watch_status(root)
    daemon_healthy_before = (
        watch_before.running and not watch_before.degraded and pid_is_running(watch_before.pid)
    )

    if missing:
        install_grammars()

    # A stale reference fingerprint means the persisted relations were produced by
    # older extraction semantics and must be rebuilt, not incrementally reused.
    fingerprint_stale = reference_index_is_stale(root)
    force_index = force or fingerprint_stale
    should_index = not initialized_before or force_index or bool(missing)

    # A forced rebuild takes the watch lock, so a live daemon must stop first;
    # schema migration also only ever runs on this indexing path.
    daemon_alive = watch_before.running and pid_is_running(watch_before.pid)
    if daemon_alive and (force_index or watch_before.degraded):
        request_watch_stop(root)
        if not wait_for_watch_to_stop(root):
            msg = f"Watch daemon did not stop for {root}."
            raise WorkspaceNotReadyError(msg)

    if should_index:
        indexed = index_workspace(root, force=force_index)
        index_payload: dict[str, object] = {
            "files": indexed.total_files,
            "symbols": indexed.total_symbols,
            "languages": indexed.languages,
        }
    else:
        stats = SymbolIndex(db_path(root)).workspace_stats()
        index_payload = {
            "files": stats["files"],
            "symbols": stats["symbols"],
            "languages": stats["languages"],
        }

    daemon = ensure_watch_daemon(root)

    if not initialized_before:
        action = "initialized"
    elif should_index or not daemon_healthy_before:
        action = "repaired"
    else:
        action = "reused"

    return EnsureWorkspaceResult(
        workspace_path=str(root),
        action=action,
        initialized=True,
        daemon={
            "running": daemon.running,
            "degraded": daemon.degraded,
            "backend": daemon.backend,
            "pid": daemon.pid,
        },
        index=index_payload,
        runtime=runtime_provenance().to_payload(),
    )
