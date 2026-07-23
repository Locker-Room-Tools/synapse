"""Tests for detached watch daemon lifecycle management."""

from pathlib import Path

import pytest

from synapse.core.watch import daemon
from synapse.core.watch.daemon import WatchDaemonError
from synapse.core.watch.state import WatchStatus
from synapse.core.workspace import workspace_id


def _status(
    workspace: Path,
    *,
    running: bool,
    pid: int | None = None,
    degraded: bool = False,
) -> WatchStatus:
    return WatchStatus(
        workspace_path=str(workspace),
        workspace_id=workspace_id(workspace),
        running=running,
        backend="polling" if running else "none",
        degraded=degraded,
        pending=0,
        pid=pid,
        started_at=None,
        stopped_at=None,
        last_event_ts=None,
        last_full_sweep_ts=None,
        last_reconcile_started_at=None,
        last_reconcile_finished_at=None,
        errors_count=0,
        errors=[],
    )


@pytest.mark.parametrize(
    ("running", "pid_alive", "expected"),
    [
        (True, True, True),
        (True, False, False),
        (False, True, False),
    ],
)
def test_watch_is_running_requires_persisted_and_live_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    running: bool,
    pid_alive: bool,
    expected: bool,
) -> None:
    """Persisted state alone never treats a dead process as a healthy daemon."""
    monkeypatch.setattr(
        daemon,
        "read_watch_status",
        lambda path: _status(tmp_path, running=running),
    )
    monkeypatch.setattr(daemon, "pid_is_running", lambda pid: pid_alive)

    assert daemon.watch_is_running(tmp_path) is expected


def test_start_detached_watch_returns_existing_pid_without_spawning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated starts reuse the live workspace daemon."""
    monkeypatch.setattr(daemon, "watch_is_running", lambda path: True)
    monkeypatch.setattr(
        daemon,
        "read_watch_status",
        lambda path: _status(tmp_path, running=True, pid=4321),
    )
    monkeypatch.setattr(
        "synapse.core.watch.daemon.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("spawned duplicate daemon"),
    )

    assert daemon.start_detached_watch(tmp_path) == 4321


def test_start_detached_watch_builds_posix_foreground_child_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSIX detachment starts a new session and redirects the child to its watch log."""
    seen: dict[str, object] = {}
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setattr(daemon, "watch_is_running", lambda path: False)
    monkeypatch.setattr("synapse.core.watch.daemon.sys.platform", "linux")

    class FakeProcess:
        pid = 4321

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        seen["command"] = command
        seen.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr("synapse.core.watch.daemon.subprocess.Popen", fake_popen)

    pid = daemon.start_detached_watch(tmp_path, poll_interval_s=2)

    command = seen["command"]
    assert pid == 4321
    assert isinstance(command, list)
    assert command[-3:] == ["--foreground", "--poll-interval", "2"]
    assert seen["cwd"] == str(tmp_path)
    assert seen["start_new_session"] is True
    assert "creationflags" not in seen
    assert (tmp_path / "data-root").is_dir()


def test_start_detached_watch_uses_windows_process_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows detachment uses process-group flags instead of POSIX sessions."""
    seen: dict[str, object] = {}
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setattr(daemon, "watch_is_running", lambda path: False)
    monkeypatch.setattr("synapse.core.watch.daemon.sys.platform", "win32")

    class FakeProcess:
        pid = 4321

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        seen.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr("synapse.core.watch.daemon.subprocess.Popen", fake_popen)

    daemon.start_detached_watch(tmp_path)

    assert seen["creationflags"] == 0x00000008 | 0x00000200
    assert "start_new_session" not in seen


def test_start_detached_watch_wraps_process_creation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OS process failures expose a lifecycle-specific actionable exception."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setattr(daemon, "watch_is_running", lambda path: False)

    def fail_popen(*args: object, **kwargs: object) -> None:
        raise OSError("process unavailable")

    monkeypatch.setattr("synapse.core.watch.daemon.subprocess.Popen", fail_popen)

    with pytest.raises(WatchDaemonError, match="process unavailable"):
        daemon.start_detached_watch(tmp_path)


def test_ensure_watch_daemon_is_idempotent_for_healthy_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy persisted daemon returns immediately without a second spawn."""
    healthy = _status(tmp_path, running=True, pid=111)
    monkeypatch.setattr(daemon, "read_watch_status", lambda path: healthy)
    monkeypatch.setattr(daemon, "pid_is_running", lambda pid: True)
    monkeypatch.setattr(
        daemon,
        "start_detached_watch",
        lambda *args, **kwargs: pytest.fail("spawned duplicate daemon"),
    )

    assert daemon.ensure_watch_daemon(tmp_path) is healthy


def test_ensure_watch_daemon_rejects_live_degraded_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live but degraded watcher cannot satisfy the freshness contract."""
    degraded = _status(tmp_path, running=True, pid=111, degraded=True)
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setattr(daemon, "read_watch_status", lambda path: degraded)
    monkeypatch.setattr(daemon, "pid_is_running", lambda pid: True)
    monkeypatch.setattr(
        daemon,
        "start_detached_watch",
        lambda *args, **kwargs: pytest.fail("spawned around degraded daemon"),
    )

    with pytest.raises(WatchDaemonError, match="is degraded"):
        daemon.ensure_watch_daemon(tmp_path)


def test_ensure_watch_daemon_waits_until_spawned_process_is_healthy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness waits for the child to publish live watch state before returning."""
    stopped = _status(tmp_path, running=False)
    healthy = _status(tmp_path, running=True, pid=222)
    statuses = iter([stopped, healthy])
    monkeypatch.setattr(daemon, "read_watch_status", lambda path: next(statuses))
    monkeypatch.setattr(daemon, "start_detached_watch", lambda path, **kwargs: 222)
    monkeypatch.setattr(daemon, "pid_is_running", lambda pid: pid == 222)
    monotonic_values = iter([0.0, 0.1])
    monkeypatch.setattr(
        "synapse.core.watch.daemon.time.monotonic",
        lambda: next(monotonic_values),
    )

    result = daemon.ensure_watch_daemon(tmp_path, timeout_s=1)

    assert result is healthy


def test_ensure_watch_daemon_timeout_reports_log_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that never publishes health fails after the bounded timeout."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    stopped = _status(tmp_path, running=False)
    monkeypatch.setattr(daemon, "read_watch_status", lambda path: stopped)
    monkeypatch.setattr(daemon, "start_detached_watch", lambda path, **kwargs: 333)

    with pytest.raises(WatchDaemonError, match=r"within 0s.*watch\.log"):
        daemon.ensure_watch_daemon(tmp_path, timeout_s=0)


def test_ensure_watch_daemon_fails_early_when_child_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that exits before readiness does not consume the full startup timeout."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    stopped = _status(tmp_path, running=False)
    monkeypatch.setattr(daemon, "read_watch_status", lambda path: stopped)
    monkeypatch.setattr(daemon, "start_detached_watch", lambda path, **kwargs: 444)
    monkeypatch.setattr(daemon, "pid_is_running", lambda pid: False)
    monotonic_values = iter([0.0, 0.1])
    monkeypatch.setattr(
        "synapse.core.watch.daemon.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(
        "synapse.core.watch.daemon.time.sleep",
        lambda seconds: pytest.fail("slept after child exit"),
    )

    with pytest.raises(WatchDaemonError, match="did not become healthy"):
        daemon.ensure_watch_daemon(tmp_path, timeout_s=5)


def test_wait_for_watch_to_stop_returns_final_state_after_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bounded stop wait performs one final liveness check at the deadline."""
    states = iter([True, False])
    monkeypatch.setattr(daemon, "watch_is_running", lambda path: next(states))
    monotonic_values = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(
        "synapse.core.watch.daemon.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr("synapse.core.watch.daemon.time.sleep", lambda seconds: None)

    assert daemon.wait_for_watch_to_stop(tmp_path, timeout_s=0.5)
