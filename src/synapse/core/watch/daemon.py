"""Detached watch daemon process lifecycle."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from synapse.core.watch.state import WatchStatus, pid_is_running, read_watch_status
from synapse.core.workspace import logs_dir, require_workspace_path

DEFAULT_START_TIMEOUT_S = 5.0
_POLL_INTERVAL_S = 0.05


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
        if status.degraded:
            msg = (
                f"Watch daemon for {root} is degraded. "
                f"Restart it and inspect {logs_dir(root) / 'watch.log'}."
            )
            raise WatchDaemonError(msg)
        return status

    spawned_pid = start_detached_watch(root, poll_interval_s=poll_interval_s)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = read_watch_status(root)
        if status.running and not status.degraded and pid_is_running(status.pid):
            return status
        if not pid_is_running(spawned_pid):
            break
        time.sleep(_POLL_INTERVAL_S)

    log_path = logs_dir(root) / "watch.log"
    msg = (
        f"Watch daemon did not become healthy for {root} within {timeout_s:g}s. "
        f"Inspect {log_path}."
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
