"""Workspace watch daemon core components."""

from synapse.core.watch.daemon import (
    WatchDaemonError,
    ensure_watch_daemon,
    start_detached_watch,
    wait_for_watch_to_stop,
    watch_is_running,
)
from synapse.core.watch.debounce import WatchBatch
from synapse.core.watch.reconcile import ReconcileResult, reconcile_workspace
from synapse.core.watch.state import (
    WatchStatus,
    WriterProvenance,
    current_writer_provenance,
    read_watch_status,
    watch_status_payload,
    watch_writer_is_current,
    watch_writer_reason,
)
from synapse.core.watch.supervisor import WatchAlreadyRunning, run_watch_foreground
from synapse.core.watch.worker import WatchBatchResult, WatchWorker

__all__ = [
    "ReconcileResult",
    "WatchAlreadyRunning",
    "WatchBatch",
    "WatchBatchResult",
    "WatchDaemonError",
    "WatchStatus",
    "WatchWorker",
    "WriterProvenance",
    "current_writer_provenance",
    "ensure_watch_daemon",
    "read_watch_status",
    "reconcile_workspace",
    "run_watch_foreground",
    "start_detached_watch",
    "wait_for_watch_to_stop",
    "watch_is_running",
    "watch_status_payload",
    "watch_writer_is_current",
    "watch_writer_reason",
]
