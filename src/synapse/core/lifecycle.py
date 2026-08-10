"""Per-workspace initialization and readiness lifecycle."""

import sqlite3
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from time import monotonic, sleep

from synapse.core.config import IgnoreWriteResult
from synapse.core.config.ignore_presets import bootstrap_project_ignores
from synapse.core.index import SymbolIndex
from synapse.core.indexing import index_workspace, reference_index_is_stale
from synapse.core.languages.grammar_install import install_grammars, missing_grammars
from synapse.core.provenance import runtime_provenance
from synapse.core.watch.daemon import ensure_watch_daemon, wait_for_watch_to_stop
from synapse.core.watch.state import pid_is_running, read_watch_status, watch_status_payload
from synapse.core.watch.supervisor import WatchAlreadyRunning, request_watch_stop
from synapse.core.workspace import (
    db_file_path,
    db_path,
    read_metadata,
    require_workspace_path,
)


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
    # Set only when first-run initialization created a .synapseignore in the repository.
    ignore_bootstrap: dict[str, object] | None = None

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
            "Call synapse_ensure_workspace (or a navigation tool, which initializes "
            "automatically) before using query tools."
        )
        raise WorkspaceNotReadyError(msg)
    return root


# A lost repair race is resolved by waiting for the winner's atomic rebuild, not by
# failing: an agent has no way to act on a lock collision it never caused.
NAVIGATION_REPAIR_TIMEOUT_S = 120.0
_NAVIGATION_POLL_INTERVAL_S = 0.2


def navigation_repair_reason(workspace_path: str | Path) -> str | None:
    """Return why a navigation call must repair this workspace, or None when it is ready.

    Readiness for navigation is more than metadata plus daemon health. Schema migration
    runs on any ``SymbolIndex`` construction, so a workspace can report READY while its
    relations were produced under older extraction semantics; serving those forever is
    the failure this check exists to prevent.

    Strictly read-only: it must never create, migrate, or write the index, because it
    runs while a watch daemon or a concurrent rebuild may own the database. Checks are
    ordered cheapest-first, so a healthy workspace pays one stat, one status-file read,
    one grammar listing, and one read-only SQLite probe.
    """
    root = require_workspace_path(workspace_path)
    if not db_file_path(root).exists():
        # Metadata can outlive a deleted cache, and the staleness probe reads such a
        # workspace as fresh because it has no stored fingerprint to disagree with.
        return "no-index"
    if workspace_status_payload(root)["state"] != WorkspaceState.READY:
        return "not-ready"
    if missing_grammars():
        return "missing-grammars"
    if reference_index_is_stale(root):
        return "stale-references"
    return None


def _await_concurrent_repair(root: Path) -> bool:
    """Wait out another process's repair, reporting whether the workspace became ready."""
    deadline = monotonic() + NAVIGATION_REPAIR_TIMEOUT_S
    while True:
        if navigation_repair_reason(root) is None:
            return True
        if monotonic() >= deadline:
            return False
        sleep(_NAVIGATION_POLL_INTERVAL_S)


def ensure_navigation_ready(workspace_path: str | Path) -> Path:
    """Return a workspace that is queryable and current, repairing it only when needed.

    This is the complete readiness decision for the navigation tools, so the MCP layer
    stays a delegator. A healthy, current workspace is never re-ensured and never
    re-indexed. A repair runs through ``ensure_workspace``, which owns grammar
    installation, the daemon stop, the watch lock, and the atomic rebuild.

    Another process may be performing exactly that repair. Losing the lock race is not
    an error as long as the workspace is queryable and current afterwards, so the
    reason is probed once more before failing.
    """
    root = require_workspace_path(workspace_path)
    reason = navigation_repair_reason(root)
    if reason is None:
        return root
    try:
        ensure_workspace(root)
    except (WatchAlreadyRunning, WorkspaceNotReadyError, sqlite3.OperationalError) as exc:
        # The winner stops the daemon, rebuilds under the watch lock, and swaps the
        # database atomically, so the workspace reads as not-ready until it finishes.
        if not _await_concurrent_repair(root):
            msg = (
                f"Synapse workspace is {reason} and a concurrent repair did not finish "
                f"within {NAVIGATION_REPAIR_TIMEOUT_S:.0f}s ({exc}). "
                "Retry, or call synapse_ensure_workspace."
            )
            raise WorkspaceNotReadyError(msg) from exc
        return root
    remaining = navigation_repair_reason(root)
    if remaining is not None:
        msg = (
            f"Synapse workspace is still {remaining} after an automatic repair. "
            "Call synapse_ensure_workspace or run 'synapse doctor'."
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

    # Seed ignore rules before the first crawl, so the initial index already honors them.
    bootstrap = bootstrap_project_ignores(root) if not initialized_before else None

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
        ignore_bootstrap=_bootstrap_payload(bootstrap),
    )


def _bootstrap_payload(result: IgnoreWriteResult | None) -> dict[str, object] | None:
    """Report a generated .synapseignore, since it is a write into the user's repository."""
    if result is None:
        return None
    return {"path": str(result.path), "patterns": len(result.added)}
