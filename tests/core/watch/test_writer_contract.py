"""Writer provenance in watch status: unknown must never read as current.

A daemon started from an older build keeps performing incremental writes under an older
persistence contract. Its status file is the only evidence the current runtime has, so
absent or malformed provenance has to be conservative.
"""

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from synapse.core.index import INDEX_WRITER_CONTRACT_VERSION
from synapse.core.watch.state import (
    current_writer_provenance,
    default_watch_status,
    read_watch_status,
    watch_status_payload,
    watch_writer_is_current,
    watch_writer_reason,
    write_watch_status,
)
from synapse.core.workspace import watch_state_path, write_metadata


def _write_raw_status(workspace: Path, payload: dict[str, Any]) -> None:
    status_path = watch_state_path(workspace)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _running_payload(workspace: Path, **extra: Any) -> dict[str, Any]:
    """The status document an older build wrote: running, with no writer identity."""
    return {
        "workspace_path": str(workspace),
        "workspace_id": "test-workspace",
        "running": True,
        "backend": "polling",
        "degraded": False,
        "pending": 0,
        "pid": 4321,
        "started_at": "2026-08-10T00:00:00Z",
        "stopped_at": None,
        "last_event_ts": None,
        "last_full_sweep_ts": "2026-08-10T00:00:01Z",
        "last_reconcile_started_at": None,
        "last_reconcile_finished_at": None,
        "errors_count": 0,
        "errors": [],
        **extra,
    }


def test_status_file_without_writer_provenance_reads_as_unknown(tmp_path: Path) -> None:
    """An old status document is readable, and its writer is unknown, not current."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_raw_status(workspace, _running_payload(workspace))

    status = read_watch_status(workspace)

    assert status.running is True
    assert status.pid == 4321
    assert status.writer_contract_version is None
    assert watch_writer_reason(status) == "writer-contract-unknown"
    assert watch_writer_is_current(status) is False


@pytest.mark.parametrize(
    "recorded",
    [None, "1", True, 1.0, [1], {"version": 1}],
    ids=["null", "text", "bool", "float", "list", "object"],
)
def test_malformed_writer_provenance_reads_as_unknown(tmp_path: Path, recorded: object) -> None:
    """Anything that is not a plain integer is unknown rather than optimistically parsed."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_raw_status(workspace, _running_payload(workspace, writer_contract_version=recorded))

    status = read_watch_status(workspace)

    assert status.writer_contract_version is None
    assert watch_writer_reason(status) == "writer-contract-unknown"


def test_an_older_writer_contract_is_a_mismatch(tmp_path: Path) -> None:
    """A well-formed but different contract is incompatible, and says which way."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    older = INDEX_WRITER_CONTRACT_VERSION - 1
    newer = INDEX_WRITER_CONTRACT_VERSION + 1

    for recorded in (older, newer):
        _write_raw_status(workspace, _running_payload(workspace, writer_contract_version=recorded))
        status = read_watch_status(workspace)
        assert status.writer_contract_version == recorded
        # Equality, not a lower bound: a newer daemon maintains invariants this runtime
        # cannot verify, so it is not reusable either.
        assert watch_writer_reason(status) == "writer-contract-mismatch"


def test_current_runtime_records_a_matching_contract(tmp_path: Path) -> None:
    """A status written by this runtime round-trips as current."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    writer = current_writer_provenance()
    assert writer.contract_version == INDEX_WRITER_CONTRACT_VERSION

    status = replace(
        default_watch_status(workspace),
        running=True,
        pid=4321,
        writer_contract_version=writer.contract_version,
        writer_package_version=writer.package_version,
        writer_package_location=writer.package_location,
    )
    write_watch_status(workspace, status)

    reloaded = read_watch_status(workspace)
    assert watch_writer_reason(reloaded) is None
    assert watch_writer_is_current(reloaded) is True


def test_status_payload_reports_the_expected_and_observed_contract(tmp_path: Path) -> None:
    """The existing status result explains a restart without a new MCP tool."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_metadata(workspace, last_indexed_at=None, languages=["python"])
    # A live pid, so the payload's own liveness override cannot be what fails the check.
    _write_raw_status(workspace, _running_payload(workspace, pid=os.getpid()))

    payload = watch_status_payload(workspace)

    assert payload["running"] is True
    assert payload["writer_contract_expected"] == INDEX_WRITER_CONTRACT_VERSION
    assert payload["writer_contract_current"] is False
    assert payload["writer_contract_version"] is None
