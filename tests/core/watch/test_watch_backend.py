"""Tests for the watch backend seam."""

from pathlib import Path

from synapse.core.watch.backend import PollingWatchBackend, RawEvent, RawEventKind


def test_polling_backend_satisfies_watch_backend_contract(tmp_path: Path) -> None:
    """The polling backend exposes the WatchBackend surface and is a safe no-op."""
    backend = PollingWatchBackend(tmp_path)

    assert backend.name == "polling"
    assert backend.degraded is False
    assert backend.root == tmp_path
    backend.start()
    backend.start()
    backend.stop()
    backend.stop()


def test_raw_event_defaults_and_kinds() -> None:
    """Raw events carry an optional rename source and cover all backend kinds."""
    event = RawEvent(path=Path("a.py"), kind=RawEventKind.CREATED, timestamp=1.0)
    assert event.old_path is None

    renamed = RawEvent(
        path=Path("b.py"),
        kind=RawEventKind.RENAMED,
        timestamp=2.0,
        old_path=Path("a.py"),
    )
    assert renamed.old_path == Path("a.py")
    assert {kind.value for kind in RawEventKind} == {
        "created",
        "modified",
        "deleted",
        "renamed",
        "overflow",
    }
