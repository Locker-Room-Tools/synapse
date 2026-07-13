"""Persistent watch daemon status and journal helpers."""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from synapse.core.workspace import (
    normalize_workspace_path,
    watch_journal_path,
    watch_state_path,
    workspace_id,
)


def utc_now() -> str:
    """Return the current UTC timestamp as ISO-8601 text."""
    return datetime.now(tz=UTC).isoformat()


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class WatchStatus:
    """Persisted status for one workspace watch daemon."""

    workspace_path: str
    workspace_id: str
    running: bool
    backend: str
    degraded: bool
    pending: int
    pid: int | None
    started_at: str | None
    stopped_at: str | None
    last_event_ts: str | None
    last_full_sweep_ts: str | None
    last_reconcile_started_at: str | None
    last_reconcile_finished_at: str | None
    errors_count: int
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class JournalIntent:
    """Unfinished batch intent loaded from the watch journal."""

    batch_id: str
    reindex_paths: list[str]
    remove_paths: list[str]


def default_watch_status(path: str | Path) -> WatchStatus:
    """Return a not-running status for a workspace."""
    root = normalize_workspace_path(path)
    return WatchStatus(
        workspace_path=str(root),
        workspace_id=workspace_id(root),
        running=False,
        backend="none",
        degraded=False,
        pending=0,
        pid=None,
        started_at=None,
        stopped_at=None,
        last_event_ts=None,
        last_full_sweep_ts=None,
        last_reconcile_started_at=None,
        last_reconcile_finished_at=None,
        errors_count=0,
        errors=[],
    )


def read_watch_status(path: str | Path) -> WatchStatus:
    """Load watch status, returning a not-running default when absent."""
    root = normalize_workspace_path(path)
    status_path = watch_state_path(root)
    if not status_path.exists():
        return default_watch_status(root)
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    errors = payload.get("errors", [])
    if not isinstance(errors, list):
        errors = []
    return WatchStatus(
        workspace_path=str(payload.get("workspace_path", root)),
        workspace_id=str(payload.get("workspace_id", workspace_id(root))),
        running=bool(payload.get("running", False)),
        backend=str(payload.get("backend", "none")),
        degraded=bool(payload.get("degraded", False)),
        pending=int(payload.get("pending", 0)),
        pid=int(payload["pid"]) if payload.get("pid") is not None else None,
        started_at=payload.get("started_at"),
        stopped_at=payload.get("stopped_at"),
        last_event_ts=payload.get("last_event_ts"),
        last_full_sweep_ts=payload.get("last_full_sweep_ts"),
        last_reconcile_started_at=payload.get("last_reconcile_started_at"),
        last_reconcile_finished_at=payload.get("last_reconcile_finished_at"),
        errors_count=int(payload.get("errors_count", len(errors))),
        errors=[str(error) for error in errors[-10:]],
    )


def write_watch_status(path: str | Path, status: WatchStatus) -> None:
    """Persist watch status atomically enough for read-only status checks."""
    status_path = watch_state_path(path)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = status_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(asdict(status), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary_path.replace(status_path)


def watch_status_payload(path: str | Path) -> dict[str, object]:
    """Return a token-frugal status payload for MCP and CLI JSON output."""
    status = read_watch_status(path)
    payload: dict[str, object] = asdict(status)
    if status.running and not _pid_is_running(status.pid):
        payload["running"] = False
        payload["pending"] = 0
    latest = _parse_timestamp(status.last_full_sweep_ts or status.last_event_ts)
    payload["staleness_seconds"] = (
        None if latest is None else max(0, int((datetime.now(tz=UTC) - latest).total_seconds()))
    )
    return payload


def _pid_is_running(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def append_journal_intent(
    path: str | Path,
    *,
    batch_id: str,
    reindex_paths: list[str],
    remove_paths: list[str],
) -> None:
    """Append a batch intent record before applying a transaction."""
    _append_journal_record(
        path,
        {
            "type": "intent",
            "batch_id": batch_id,
            "reindex_paths": reindex_paths,
            "remove_paths": remove_paths,
            "timestamp": utc_now(),
        },
    )


def append_journal_complete(path: str | Path, *, batch_id: str) -> None:
    """Append a batch completion marker after committing a transaction."""
    _append_journal_record(
        path,
        {"type": "complete", "batch_id": batch_id, "timestamp": utc_now()},
    )


def _append_journal_record(path: str | Path, payload: dict[str, object]) -> None:
    journal_path = watch_journal_path(path)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def read_unfinished_journal(path: str | Path) -> list[JournalIntent]:
    """Return journaled batch intents without matching completion markers."""
    journal_path = watch_journal_path(path)
    if not journal_path.exists():
        return []
    intents: dict[str, JournalIntent] = {}
    completed: set[str] = set()
    for line in journal_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload: dict[str, Any] = json.loads(line)
        batch_id = str(payload.get("batch_id", ""))
        if not batch_id:
            continue
        if payload.get("type") == "complete":
            completed.add(batch_id)
            continue
        if payload.get("type") != "intent":
            continue
        reindex = payload.get("reindex_paths", [])
        remove = payload.get("remove_paths", [])
        if isinstance(reindex, list) and isinstance(remove, list):
            intents[batch_id] = JournalIntent(
                batch_id=batch_id,
                reindex_paths=[str(item) for item in reindex],
                remove_paths=[str(item) for item in remove],
            )
    return [intent for batch_id, intent in intents.items() if batch_id not in completed]


def truncate_journal(path: str | Path) -> None:
    """Clear the watch journal after startup replay/reconciliation."""
    journal_path = watch_journal_path(path)
    journal_path.unlink(missing_ok=True)
