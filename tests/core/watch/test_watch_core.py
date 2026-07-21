"""Tests for watch daemon core components."""

from pathlib import Path

import pytest

from synapse.core.crawler import hash_source as calculate_source_hash
from synapse.core.index import SymbolIndex
from synapse.core.parser import ParsedSource
from synapse.core.parser import parse_source as parse_source_bytes
from synapse.core.watch.debounce import CoalescingBuffer
from synapse.core.watch.events import ChangeEvent, ChangeKind, EventNormalizer
from synapse.core.watch.reconcile import reconcile_workspace
from synapse.core.watch.state import (
    WatchStatus,
    append_journal_complete,
    append_journal_intent,
    read_unfinished_journal,
    read_watch_status,
    utc_now,
    watch_status_payload,
    write_watch_status,
)
from synapse.core.watch.supervisor import (
    WatchAlreadyRunning,
    WatchLock,
    request_watch_stop,
    run_watch_foreground,
)
from synapse.core.watch.worker import WatchWorker
from synapse.core.workspace import db_path, watch_journal_path, watch_lock_path, workspace_id


def _status(workspace_root: Path, *, running: bool, pid: int | None = None) -> WatchStatus:
    return WatchStatus(
        workspace_path=str(workspace_root),
        workspace_id=workspace_id(workspace_root),
        running=running,
        backend="polling",
        degraded=False,
        pending=3 if running else 0,
        pid=pid,
        started_at=utc_now() if running else None,
        stopped_at=None,
        last_event_ts=None,
        last_full_sweep_ts="2026-01-01T00:00:00+00:00",
        last_reconcile_started_at=None,
        last_reconcile_finished_at=None,
        errors_count=0,
        errors=[],
    )


def test_event_normalizer_filters_ignored_temp_and_unsupported_paths(tmp_path: Path) -> None:
    """Only supported source files inside non-ignored directories pass normalization."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    normalizer = EventNormalizer(workspace_root, frozenset({"node_modules"}))

    assert normalizer.normalize_path(workspace_root / "sample.py") == "sample.py"
    assert normalizer.normalize_path(workspace_root / "sample.py.tmp") is None
    assert normalizer.normalize_path(workspace_root / "README.txt") is None
    assert normalizer.normalize_path(workspace_root / "node_modules" / "lib.py") is None


def test_event_normalizer_handles_rename_delete_and_outside_paths(tmp_path: Path) -> None:
    """Rename/delete normalization stays workspace-relative and filters unsafe paths."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    normalizer = EventNormalizer(workspace_root, frozenset())

    delete = normalizer.normalize(ChangeKind.DELETE, workspace_root / "sample.py", timestamp=1.0)
    renamed_to_unsupported = normalizer.normalize(
        ChangeKind.RENAME,
        workspace_root / "README.md",
        old_path=workspace_root / "old.py",
        timestamp=2.0,
    )

    assert delete == ChangeEvent(ChangeKind.DELETE, "sample.py", 1.0)
    assert renamed_to_unsupported == ChangeEvent(ChangeKind.RENAME, None, 2.0, "old.py")
    assert normalizer.normalize_path(tmp_path / "outside.py") is None


def test_event_normalizer_preserves_a_file_symlink_workspace_path(tmp_path: Path) -> None:
    """Watch events identify a symlink lexically instead of leaking its target."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = tmp_path / "outside.py"
    target.write_text("def linked(): pass\n", encoding="utf-8")
    link = workspace / "linked.py"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")

    assert EventNormalizer(workspace, frozenset()).normalize_path(link) == "linked.py"


def test_coalescing_buffer_batches_latest_intent() -> None:
    """Debounce coalescing keeps the last path intent and emits bounded batches."""
    buffer = CoalescingBuffer(debounce_ms=100, max_latency_ms=1_000, batch_size=10)

    buffer.add(ChangeEvent(ChangeKind.CREATE, "sample.py", 0.0))
    buffer.add(ChangeEvent(ChangeKind.MODIFY, "sample.py", 0.05))
    buffer.add(ChangeEvent(ChangeKind.RENAME, "new.py", 0.1, old_rel_path="old.py"))
    batch = buffer.flush_ready(now=0.25)

    assert batch.reindex_paths == ["new.py", "sample.py"]
    assert batch.remove_paths == ["old.py"]
    assert buffer.pending_count() == 0


def test_coalescing_buffer_covers_cancellation_latency_batching_and_overflow() -> None:
    """Future event mode behavior is pinned even while supervisor uses polling."""
    buffer = CoalescingBuffer(debounce_ms=100, max_latency_ms=500, batch_size=1)

    buffer.add(ChangeEvent(ChangeKind.CREATE, "created.py", 0.0))
    buffer.add(ChangeEvent(ChangeKind.DELETE, "created.py", 0.05))
    buffer.add(ChangeEvent(ChangeKind.MODIFY, "stale.py", 0.0))
    buffer.add(ChangeEvent(ChangeKind.OVERFLOW, None, 0.0))

    first = buffer.flush_ready(now=0.55)
    second = buffer.flush_ready(now=0.55, force=True)

    assert first.remove_paths == ["created.py"]
    assert first.reindex_paths == []
    assert second.reindex_paths == ["stale.py"]
    assert buffer.pending_count() == 0


def test_watch_worker_indexes_skips_updates_and_removes_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The watch worker applies idempotent file-grained index updates."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    source_path = workspace_root / "sample.py"
    source_path.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    worker = WatchWorker(workspace_root)

    first = worker.apply_batch(reindex_paths=["sample.py"], remove_paths=[])
    second = worker.apply_batch(reindex_paths=["sample.py"], remove_paths=[])
    source_path.write_text("def beta():\n    return 2\n", encoding="utf-8")
    third = worker.apply_batch(reindex_paths=["sample.py"], remove_paths=[])
    source_path.unlink()
    fourth = worker.apply_batch(reindex_paths=[], remove_paths=["sample.py"])

    index = SymbolIndex(db_path(workspace_root))
    assert (first.indexed_files, second.skipped_files, third.indexed_files) == (1, 1, 1)
    assert fourth.removed_files == 1
    assert index.list_indexed_files() == []


def test_watch_worker_rejects_missing_workspace_before_cache_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Watch initialization validates its workspace before allocating state."""
    data_root = tmp_path / "data-root"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))

    with pytest.raises(NotADirectoryError, match="Workspace is not a directory"):
        WatchWorker(tmp_path / "missing")

    assert not data_root.exists()


def test_watch_worker_reindex_of_missing_path_removes_existing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a path appears in both intents, missing-file reindex still converges deletes."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    source_path = workspace_root / "sample.py"
    source_path.write_text("def alpha(): pass\n", encoding="utf-8")
    worker = WatchWorker(workspace_root)
    worker.apply_batch(reindex_paths=["sample.py"], remove_paths=[])
    source_path.unlink()

    result = worker.apply_batch(reindex_paths=["sample.py"], remove_paths=["sample.py"])

    assert result.removed_files == 1
    assert SymbolIndex(db_path(workspace_root)).list_indexed_files() == []


def test_watch_reference_reconciliation_handles_add_rename_and_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Watch batches update references in callers omitted from the event batch."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    caller = workspace / "caller.py"
    target = workspace / "target.py"
    caller.write_text("def caller():\n    return helper()\n", encoding="utf-8")
    worker = WatchWorker(workspace)

    worker.apply_batch(reindex_paths=["caller.py"], remove_paths=[])
    assert len(worker.index.get_references_by_name("helper")) == 1

    target.write_text("def helper():\n    return 1\n", encoding="utf-8")
    worker.apply_batch(reindex_paths=["target.py"], remove_paths=[])
    helper = worker.index.get_definition("helper")[0]
    assert len(worker.index.get_references(helper.id)) == 1

    target.write_text("def renamed():\n    return 1\n", encoding="utf-8")
    worker.apply_batch(reindex_paths=["target.py"], remove_paths=[])
    assert len(worker.index.get_references_by_name("helper")) == 1

    caller.write_text("def caller():\n    return renamed()\n", encoding="utf-8")
    worker.apply_batch(reindex_paths=["caller.py"], remove_paths=[])
    renamed = worker.index.get_definition("renamed")[0]
    assert len(worker.index.get_references(renamed.id)) == 1

    target.unlink()
    worker.apply_batch(reindex_paths=[], remove_paths=["target.py"])
    assert len(worker.index.get_references_by_name("renamed")) == 1


def test_watch_reference_reconciliation_has_no_256_file_cutoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every dependent caller is reconciled when a target name changes."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.py"
    target.write_text("def helper():\n    return 1\n", encoding="utf-8")
    caller_paths: list[str] = []
    for index in range(257):
        relative_path = f"caller_{index:03}.py"
        caller_paths.append(relative_path)
        (workspace / relative_path).write_text(
            f"def caller_{index}():\n    return helper()\n",
            encoding="utf-8",
        )
    worker = WatchWorker(workspace)
    worker.apply_batch(
        reindex_paths=[*caller_paths, "target.py"],
        remove_paths=[],
    )
    helper = worker.index.get_definition("helper")[0]
    assert len(worker.index.get_references(helper.id)) == 257

    target.write_text("def renamed():\n    return 1\n", encoding="utf-8")
    worker.apply_batch(reindex_paths=["target.py"], remove_paths=[])

    assert len(worker.index.get_references_by_name("helper")) == 257
    assert worker.index.get_references(helper.id) == []


def test_watch_reconcile_reads_hashes_and_parses_changed_file_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polling reconciliation passes changed files through one worker read and parse."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "sample.py"
    source.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    reconcile_workspace(workspace)
    source.write_text("def beta():\n    return 2\n", encoding="utf-8")
    original_read_bytes = Path.read_bytes
    calls = {"read": 0, "hash": 0, "parse": 0}

    def counting_read_bytes(path: Path) -> bytes:
        if path == source:
            calls["read"] += 1
        return original_read_bytes(path)

    def counting_hash_source(source_bytes: bytes) -> str:
        calls["hash"] += 1
        return calculate_source_hash(source_bytes)

    def counting_parse_source(
        path: Path,
        language: str,
        source_bytes: bytes,
        workspace_root: Path | None = None,
    ) -> ParsedSource:
        calls["parse"] += 1
        return parse_source_bytes(path, language, source_bytes, workspace_root)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)
    monkeypatch.setattr("synapse.core.watch.reconcile.hash_source", counting_hash_source)
    monkeypatch.setattr("synapse.core.watch.worker.parse_source", counting_parse_source)

    reconcile_workspace(workspace)

    assert calls == {"read": 1, "hash": 1, "parse": 1}


def test_watch_worker_parse_failure_leaves_unfinished_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed transactions keep the intent journal for startup reconcile recovery."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "sample.py").write_text("def alpha(): pass\n", encoding="utf-8")

    def fail_parse(*_args: object, **_kwargs: object) -> list[object]:
        raise RuntimeError("parse boom")

    monkeypatch.setattr("synapse.core.watch.worker.parse_source", fail_parse)

    with pytest.raises(RuntimeError, match="parse boom"):
        WatchWorker(workspace_root).apply_batch(reindex_paths=["sample.py"], remove_paths=[])

    unfinished = read_unfinished_journal(workspace_root)
    assert len(unfinished) == 1
    assert unfinished[0].reindex_paths == ["sample.py"]


def test_reconcile_workspace_converges_index_to_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reconciliation catches creates and deletes without relying on events."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    source_path = workspace_root / "sample.py"
    source_path.write_text("def alpha():\n    return 1\n", encoding="utf-8")

    first = reconcile_workspace(workspace_root)
    source_path.unlink()
    second = reconcile_workspace(workspace_root)

    assert first.indexed_files == 1
    assert second.removed_files == 1
    assert SymbolIndex(db_path(workspace_root)).list_indexed_files() == []


def test_reconcile_workspace_handles_rename_as_delete_plus_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polling reconcile treats renames as old-path removal and new-path indexing."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    old_path = workspace_root / "old.py"
    old_path.write_text("def alpha(): pass\n", encoding="utf-8")
    reconcile_workspace(workspace_root)

    old_path.rename(workspace_root / "new.py")
    result = reconcile_workspace(workspace_root)

    indexed_paths = [
        item.path for item in SymbolIndex(db_path(workspace_root)).list_indexed_files()
    ]
    assert result.indexed_files == 1
    assert result.removed_files == 1
    assert indexed_paths == ["new.py"]


def test_reconcile_workspace_removes_files_after_ignore_config_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing ignored directories converges previously indexed files out of the DB."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    workspace_root = tmp_path / "workspace"
    generated = workspace_root / "generated"
    generated.mkdir(parents=True)
    (generated / "sample.py").write_text("def alpha(): pass\n", encoding="utf-8")
    reconcile_workspace(workspace_root)
    config_path = tmp_path / "xdg" / "synapse" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"ignored_directories": ["generated"]}\n', encoding="utf-8")

    result = reconcile_workspace(workspace_root)

    assert result.removed_files == 1
    assert SymbolIndex(db_path(workspace_root)).list_indexed_files() == []


def test_watch_journal_reports_unfinished_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Journal intents disappear from replay once a completion marker exists."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    append_journal_intent(
        workspace_root,
        batch_id="batch-1",
        reindex_paths=["sample.py"],
        remove_paths=[],
    )
    assert [item.batch_id for item in read_unfinished_journal(workspace_root)] == ["batch-1"]

    append_journal_complete(workspace_root, batch_id="batch-1")

    assert read_unfinished_journal(workspace_root) == []


def test_startup_journal_reconcile_truncates_unfinished_intents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Foreground startup recovery reconciles and clears unfinished journal intents."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "sample.py").write_text("def alpha(): pass\n", encoding="utf-8")
    append_journal_intent(
        workspace_root,
        batch_id="unfinished",
        reindex_paths=["sample.py"],
        remove_paths=[],
    )

    run_watch_foreground(workspace_root, once=True)

    assert read_unfinished_journal(workspace_root) == []
    assert not watch_journal_path(workspace_root).exists()


def test_run_watch_foreground_once_writes_stopped_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Foreground polling can run a bounded startup reconcile for supervisors/tests."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "sample.py").write_text("def alpha(): pass\n", encoding="utf-8")

    status = run_watch_foreground(workspace_root, once=True)

    persisted = read_watch_status(workspace_root)
    assert status.running is False
    assert persisted.running is False
    assert persisted.backend == "polling"
    assert SymbolIndex(db_path(workspace_root)).search_symbols("alpha")


def test_watch_lock_replaces_stale_lock_and_blocks_live_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The singleton lock removes stale files but refuses live owners."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    lock_path = watch_lock_path(workspace_root)
    lock_path.write_text("12345\n", encoding="utf-8")
    monkeypatch.setattr("synapse.core.watch.supervisor.pid_is_running", lambda pid: False)

    lock = WatchLock(workspace_root)
    lock.acquire()
    lock.release()

    lock_path.write_text("12345\n", encoding="utf-8")
    monkeypatch.setattr("synapse.core.watch.supervisor.pid_is_running", lambda pid: True)
    with pytest.raises(WatchAlreadyRunning):
        WatchLock(workspace_root).acquire()


def test_duplicate_foreground_start_does_not_overwrite_live_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed duplicate start leaves the existing daemon status intact."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    lock_path = watch_lock_path(workspace_root)
    lock_path.write_text("12345\n", encoding="utf-8")
    write_watch_status(workspace_root, _status(workspace_root, running=True, pid=12345))
    monkeypatch.setattr("synapse.core.watch.supervisor.pid_is_running", lambda pid: True)

    with pytest.raises(WatchAlreadyRunning):
        run_watch_foreground(workspace_root, once=True)

    assert read_watch_status(workspace_root).running is True


def test_request_watch_stop_is_idempotent_for_absent_and_dead_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop requests normalize missing/dead daemons to stopped status."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    missing = request_watch_stop(workspace_root)
    write_watch_status(workspace_root, _status(workspace_root, running=True, pid=999999))
    monkeypatch.setattr("synapse.core.watch.supervisor.pid_is_running", lambda pid: False)
    dead = request_watch_stop(workspace_root)

    assert missing.running is False
    assert dead.running is False
    assert read_watch_status(workspace_root).running is False


def test_request_watch_stop_signals_live_process_without_marking_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live stop requests stay advisory until the daemon writes its final status."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    write_watch_status(workspace_root, _status(workspace_root, running=True, pid=12345))
    seen: dict[str, int] = {}
    monkeypatch.setattr("synapse.core.watch.supervisor.pid_is_running", lambda pid: True)
    monkeypatch.setattr(
        "synapse.core.watch.supervisor.os.kill",
        lambda pid, sig: seen.update(pid=pid),
    )

    status = request_watch_stop(workspace_root)

    assert status.running is True
    assert seen == {"pid": 12345}


def test_watch_status_payload_marks_dead_running_status_as_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read-only status payloads do not report stale PIDs as running."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    write_watch_status(workspace_root, _status(workspace_root, running=True, pid=999999))

    payload = watch_status_payload(workspace_root)

    assert payload["running"] is False
    assert payload["pending"] == 0
    assert isinstance(payload["staleness_seconds"], int)
