"""Detached watch daemon process lifecycle."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from synapse.core.index.contract import INDEX_WRITER_CONTRACT_VERSION
from synapse.core.watch.state import (
    WatchStatus,
    pid_is_running,
    read_watch_status,
    watch_writer_reason,
)
from synapse.core.watch.supervisor import request_watch_stop
from synapse.core.workspace import logs_dir, require_workspace_path

DEFAULT_START_TIMEOUT_S = 5.0
_POLL_INTERVAL_S = 0.05
# Another installation can publish status after this runtime checked and spawned. One
# stop-and-respawn recovery is enough to win a genuine race; more would only be a contest
# between two runtimes that each consider the other stale, so the call fails explicitly.
_MAX_WRITER_RACE_RECOVERIES = 1


class WatchDaemonError(RuntimeError):
    """Raised when the workspace watch daemon cannot become healthy."""


def watch_is_running(workspace_path: str | Path) -> bool:
    """Return whether a live daemon owns the workspace watcher."""
    root = require_workspace_path(workspace_path)
    status = read_watch_status(root)
    return status.running and pid_is_running(status.pid)


def start_detached_watch(
    workspace_path: str | Path,
    *,
    poll_interval_s: int | None = None,
) -> int:
    """Start a detached foreground watcher process and return its PID."""
    root = require_workspace_path(workspace_path)
    if watch_is_running(root):
        return read_watch_status(root).pid or 0

    command = [
        sys.executable,
        "-m",
        "synapse",
        "watch",
        "start",
        "--workspace",
        str(root),
        "--foreground",
    ]
    if poll_interval_s is not None:
        command.extend(["--poll-interval", str(poll_interval_s)])

    log_path = logs_dir(root) / "watch.log"
    try:
        with log_path.open("a", encoding="utf-8") as log_handle:
            if sys.platform == "win32":
                process = subprocess.Popen(
                    command,
                    cwd=str(root),
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=log_handle,
                    creationflags=0x00000008 | 0x00000200,
                )
            else:
                process = subprocess.Popen(
                    command,
                    cwd=str(root),
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=log_handle,
                    start_new_session=True,
                )
    except OSError as exc:
        msg = f"Could not start watch daemon for {root}: {exc}"
        raise WatchDaemonError(msg) from exc
    return int(process.pid)


def _is_reusable(status: WatchStatus) -> bool:
    """Whether a recorded status may be handed back as a healthy daemon.

    The single compatibility rule, applied identically before and after a spawn: a live,
    non-degraded process whose writer contract this runtime can verify. Anything else is
    either broken or writing under a persistence contract we cannot vouch for.
    """
    return (
        status.running
        and not status.degraded
        and pid_is_running(status.pid)
        and watch_writer_reason(status) is None
    )


def _stop_incompatible_writer(root: Path, reason: str, *, timeout_s: float) -> None:
    """Stop the recorded daemon of another runtime through the supported lifecycle path."""
    request_watch_stop(root)
    if not wait_for_watch_to_stop(root, timeout_s=timeout_s):
        msg = f"Stale watch daemon ({reason}) did not stop for {root}."
        raise WatchDaemonError(msg)


def ensure_watch_daemon(
    workspace_path: str | Path,
    *,
    poll_interval_s: int | None = None,
    timeout_s: float = DEFAULT_START_TIMEOUT_S,
) -> WatchStatus:
    """Ensure a healthy detached watcher is running for the workspace."""
    root = require_workspace_path(workspace_path)
    status = read_watch_status(root)
    if status.running and pid_is_running(status.pid):
        writer_reason = watch_writer_reason(status)
        if writer_reason is None:
            if status.degraded:
                msg = (
                    f"Watch daemon for {root} is degraded. "
                    f"Restart it and inspect {logs_dir(root) / 'watch.log'}."
                )
                raise WatchDaemonError(msg)
            return status
        # A live daemon from another runtime must never be handed back as healthy: it
        # would keep writing under a persistence contract this runtime cannot verify.
        _stop_incompatible_writer(root, writer_reason, timeout_s=timeout_s)

    spawned_pid = start_detached_watch(root, poll_interval_s=poll_interval_s)
    deadline = time.monotonic() + timeout_s
    recoveries = 0
    racing_reason: str | None = None
    while time.monotonic() < deadline:
        status = read_watch_status(root)
        if _is_reusable(status):
            return status
        if status.running and pid_is_running(status.pid):
            # The status check above already rejected this daemon; only an incompatible
            # writer is worth acting on, and only by stopping it.
            racing_reason = watch_writer_reason(status)
            if racing_reason is not None:
                if recoveries >= _MAX_WRITER_RACE_RECOVERIES:
                    break
                recoveries += 1
                _stop_incompatible_writer(
                    root, racing_reason, timeout_s=max(0.0, deadline - time.monotonic())
                )
                # Our own child loses the watch lock to a competitor and exits, so it is
                # respawned once here rather than by re-entering this function.
                if not pid_is_running(spawned_pid):
                    spawned_pid = start_detached_watch(root, poll_interval_s=poll_interval_s)
                continue
        if not pid_is_running(spawned_pid):
            break
        time.sleep(_POLL_INTERVAL_S)

    if racing_reason is not None:
        msg = (
            f"Another Synapse runtime ({racing_reason}) keeps claiming the watch daemon "
            f"for {root}; this runtime implements writer contract "
            f"{INDEX_WRITER_CONTRACT_VERSION}. Stop the other installation and retry."
        )
        raise WatchDaemonError(msg)
    log_path = logs_dir(root) / "watch.log"
    msg = (
        f"Watch daemon did not become healthy for {root} within {timeout_s:g}s. Inspect {log_path}."
    )
    raise WatchDaemonError(msg)


def wait_for_watch_to_stop(
    workspace_path: str | Path,
    *,
    timeout_s: float = DEFAULT_START_TIMEOUT_S,
) -> bool:
    """Wait until the workspace watcher no longer has a live process."""
    root = require_workspace_path(workspace_path)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not watch_is_running(root):
            return True
        time.sleep(_POLL_INTERVAL_S)
    return not watch_is_running(root)
