"""Watch lifecycle, singleton lock, and polling supervisor."""

from __future__ import annotations

import os
import signal
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import FrameType
from typing import Any

from synapse.core.config import load_user_config
from synapse.core.watch.backend import PollingWatchBackend
from synapse.core.watch.reconcile import reconcile_workspace
from synapse.core.watch.state import (
    WatchStatus,
    read_unfinished_journal,
    read_watch_status,
    truncate_journal,
    utc_now,
    write_watch_status,
)
from synapse.core.workspace import normalize_workspace_path, watch_lock_path, workspace_id

type SignalHandler = Callable[[int, FrameType | None], Any] | int | signal.Handlers | None


class WatchAlreadyRunning(RuntimeError):
    """Raised when another daemon owns the workspace watch lock."""


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


class WatchLock:
    """Advisory singleton lock backed by an exclusive lock file."""

    def __init__(self, workspace_path: str | Path) -> None:
        self.root = normalize_workspace_path(workspace_path)
        self.path = watch_lock_path(self.root)
        self._fd: int | None = None

    def acquire(self) -> None:
        """Acquire the workspace watch lock or raise when a live owner exists."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError as exc:
                owner_pid = self._read_owner_pid()
                if pid_is_running(owner_pid):
                    msg = f"watch daemon already running for {self.root} (pid {owner_pid})"
                    raise WatchAlreadyRunning(msg) from exc
                self.path.unlink(missing_ok=True)
                continue
            os.write(self._fd, f"{os.getpid()}\n".encode())
            return

    def release(self) -> None:
        """Release the lock file."""
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self.path.unlink(missing_ok=True)

    def _read_owner_pid(self) -> int | None:
        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        try:
            return int(text.splitlines()[0])
        except (IndexError, ValueError):
            return None

    def __enter__(self) -> WatchLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def run_watch_foreground(
    workspace_path: str | Path = ".",
    *,
    poll_interval_s: int | None = None,
    once: bool = False,
    stop_event: threading.Event | None = None,
) -> WatchStatus:
    """Run the watch supervisor in the current process using polling reconcile mode."""
    root = normalize_workspace_path(workspace_path)
    config = load_user_config().watch
    interval = poll_interval_s or config.poll_interval_s
    backend = PollingWatchBackend(root)
    local_stop = stop_event or threading.Event()
    previous_sigterm = _install_signal_handler(local_stop)
    started_at = utc_now()
    status = _status(
        root,
        running=True,
        backend=backend.name,
        started_at=started_at,
        pid=os.getpid(),
    )
    lock = WatchLock(root)
    acquired = False
    backend_started = False

    try:
        lock.acquire()
        acquired = True
        backend.start()
        backend_started = True
        write_watch_status(root, status)
        _replay_startup_journal(root)
        while not local_stop.is_set():
            started = utc_now()
            status = _replace_status(status, last_reconcile_started_at=started)
            write_watch_status(root, status)
            try:
                reconcile_workspace(root)
                finished = utc_now()
                status = _replace_status(
                    status,
                    pending=0,
                    last_full_sweep_ts=finished,
                    last_reconcile_finished_at=finished,
                )
            except Exception as exc:
                status = _status_with_error(status, str(exc))
                write_watch_status(root, status)
                raise
            write_watch_status(root, status)
            if once:
                break
            local_stop.wait(interval)
    finally:
        if backend_started:
            backend.stop()
        if acquired:
            stopped = utc_now()
            status = _replace_status(status, running=False, stopped_at=stopped, pending=0)
            write_watch_status(root, status)
            lock.release()
        _restore_signal_handler(previous_sigterm)
    return status


def request_watch_stop(workspace_path: str | Path) -> WatchStatus:
    """Ask a foreground/detached watch daemon to stop via SIGTERM when possible."""
    root = normalize_workspace_path(workspace_path)
    status = read_watch_status(root)
    if status.running and pid_is_running(status.pid):
        assert status.pid is not None
        try:
            os.kill(status.pid, signal.SIGTERM)
            return status
        except ProcessLookupError:
            pass
    stopped = _replace_status(status, running=False, stopped_at=utc_now(), pending=0)
    write_watch_status(root, stopped)
    return stopped


def _status(
    root: Path,
    *,
    running: bool,
    backend: str,
    started_at: str | None,
    pid: int | None,
) -> WatchStatus:
    return WatchStatus(
        workspace_path=str(root),
        workspace_id=workspace_id(root),
        running=running,
        backend=backend,
        degraded=False,
        pending=0,
        pid=pid,
        started_at=started_at,
        stopped_at=None,
        last_event_ts=None,
        last_full_sweep_ts=None,
        last_reconcile_started_at=None,
        last_reconcile_finished_at=None,
        errors_count=0,
        errors=[],
    )


def _replace_status(status: WatchStatus, **updates: Any) -> WatchStatus:
    return replace(status, **updates)


def _status_with_error(status: WatchStatus, error: str) -> WatchStatus:
    errors = [*status.errors, error][-10:]
    return _replace_status(status, errors_count=status.errors_count + 1, errors=errors)


def _replay_startup_journal(root: Path) -> None:
    unfinished = read_unfinished_journal(root)
    if unfinished:
        reconcile_workspace(root)
        truncate_journal(root)


def _install_signal_handler(stop_event: threading.Event) -> SignalHandler:
    if threading.current_thread() is not threading.main_thread():
        return None
    previous = signal.getsignal(signal.SIGTERM)

    def handle_sigterm(_signum: int, _frame: FrameType | None) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_sigterm)
    return previous


def _restore_signal_handler(previous: SignalHandler) -> None:
    if previous is None or threading.current_thread() is not threading.main_thread():
        return
    signal.signal(signal.SIGTERM, previous)