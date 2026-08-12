"""Persistent watch daemon status and journal helpers."""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import synapse
from synapse.core.index.contract import INDEX_WRITER_CONTRACT_VERSION
from synapse.core.workspace import (
    normalize_workspace_path,
    read_metadata,
    require_workspace_path,
    watch_journal_path,
    watch_state_file_path,
    watch_state_path,
    workspace_id,
)


def utc_now() -> str:
    """Return the current UTC timestamp as ISO-8601 text."""
    return datetime.now(tz=UTC).isoformat()


def _parse_contract_version(value: object) -> int | None:
    """Read a writer contract version conservatively: anything odd means unknown."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _parse_optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


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
    # Identity of the process performing incremental writes. Status files written by
    # older builds carry none, which is why the default is None (unknown) rather than
    # the current contract. Only the contract version decides compatibility; the
    # package fields are diagnostics, since two development builds can share a version
    # while implementing different persistence contracts.
    writer_contract_version: int | None = None
    writer_package_version: str | None = None
    writer_package_location: str | None = None


def watch_writer_reason(status: WatchStatus) -> str | None:
    """Return why this daemon's writer cannot be trusted, or None when it matches.

    Equality rather than a lower bound: a daemon from a newer build maintains
    invariants this runtime cannot verify, so it is not reusable either.
    """
    if status.writer_contract_version is None:
        return "writer-contract-unknown"
    if status.writer_contract_version != INDEX_WRITER_CONTRACT_VERSION:
        return "writer-contract-mismatch"
    return None


def watch_writer_is_current(status: WatchStatus) -> bool:
    """Whether this daemon implements the current index write contract."""
    return watch_writer_reason(status) is None


@dataclass(frozen=True, slots=True)
class WriterProvenance:
    """Writer identity recorded by a daemon started from this runtime."""

    contract_version: int
    package_version: str
    package_location: str


def current_writer_provenance() -> WriterProvenance:
    """Return the writer identity of the current runtime."""
    return WriterProvenance(
        contract_version=INDEX_WRITER_CONTRACT_VERSION,
        package_version=synapse.__version__,
        package_location=str(Path(synapse.__file__).resolve().parent),
    )


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
    status_path = watch_state_file_path(root)
    if not status_path.exists():
        return default_watch_status(root)
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    errors = payload.get("errors", [])
    if not isinstance(errors, list):
        errors = []
    return WatchStatus(
        writer_contract_version=_parse_contract_version(payload.get("writer_contract_version")),
        writer_package_version=_parse_optional_text(payload.get("writer_package_version")),
        writer_package_location=_parse_optional_text(payload.get("writer_package_location")),
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
    root = require_workspace_path(path)
    status = read_watch_status(root)
    payload: dict[str, object] = asdict(status)
    payload["initialized"] = read_metadata(root) is not None
    payload["workspace_path"] = str(root)
    if status.running and not pid_is_running(status.pid):
        payload["running"] = False
        payload["pending"] = 0
    latest = _parse_timestamp(status.last_full_sweep_ts or status.last_event_ts)
    payload["staleness_seconds"] = (
        None if latest is None else max(0, int((datetime.now(tz=UTC) - latest).total_seconds()))
    )
    payload["writer_contract_expected"] = INDEX_WRITER_CONTRACT_VERSION
    payload["writer_contract_current"] = bool(payload["running"]) and watch_writer_is_current(
        status
    )
    return payload


def pid_is_running(pid: int | None) -> bool:
    """Return whether a process id appears alive on the local host."""
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
