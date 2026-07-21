"""Regression tests for SQLite ownership and atomic index replacement."""

import sqlite3
from pathlib import Path

import pytest

from synapse.core.index import SymbolIndex
from synapse.core.index_schema import create_connection
from synapse.core.indexing import index_workspace
from synapse.core.models import SourceFile
from synapse.core.watch.supervisor import WatchAlreadyRunning, WatchLock
from synapse.core.workspace import db_path


class _TrackingConnection(sqlite3.Connection):
    """Concrete connection subtype used to assert explicit close calls."""


def _assert_closed(connections: list[sqlite3.Connection]) -> None:
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_automatically_opened_connections_close_on_success_and_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Initialization, reads, commits, and rollbacks release their handles."""
    original_connect = sqlite3.connect
    connections: list[sqlite3.Connection] = []

    def tracked_connect(database: str | bytes | Path) -> sqlite3.Connection:
        connection = original_connect(database, factory=_TrackingConnection)
        connections.append(connection)
        return connection

    monkeypatch.setattr("synapse.core.index_schema.sqlite3.connect", tracked_connect)
    index = SymbolIndex(tmp_path / "index.sqlite")
    index.upsert_file(
        SourceFile(
            id="sample.py",
            path="sample.py",
            language="python",
            project_root=str(tmp_path),
            content_hash="one",
            indexed_at="2026-07-17T00:00:00+00:00",
        )
    )
    assert index.workspace_stats()["files"] == 1

    with pytest.raises(RuntimeError, match="rollback"), index.transaction() as connection:
        connection.execute("DELETE FROM files")
        raise RuntimeError("rollback")

    assert index.workspace_stats()["files"] == 1
    _assert_closed(connections)


def test_connection_configuration_failure_closes_the_new_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PRAGMA failure during connection setup cannot leak the raw handle."""
    connection = sqlite3.connect(tmp_path / "index.sqlite", factory=_TrackingConnection)

    def deny_pragmas(
        action_code: int,
        _argument_one: str | None,
        _argument_two: str | None,
        _database_name: str | None,
        _trigger_name: str | None,
    ) -> int:
        return sqlite3.SQLITE_DENY if action_code == sqlite3.SQLITE_PRAGMA else sqlite3.SQLITE_OK

    def return_connection(_database: str | bytes | Path) -> sqlite3.Connection:
        return connection

    connection.set_authorizer(deny_pragmas)
    monkeypatch.setattr("synapse.core.index_schema.sqlite3.connect", return_connection)

    with pytest.raises(sqlite3.DatabaseError):
        create_connection(tmp_path / "index.sqlite")

    _assert_closed([connection])


def test_forced_rebuild_checkpoints_wal_before_atomic_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rebuilt database remains complete after its temporary WAL is removed."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "sample.py"
    source.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    index_workspace(workspace)
    source.write_text("def beta():\n    return 2\n", encoding="utf-8")

    index_workspace(workspace, force=True)

    target = db_path(workspace)
    reopened = SymbolIndex(target)
    assert reopened.search_symbols("alpha") == []
    assert [symbol.name for symbol in reopened.search_symbols("beta")] == ["beta"]
    assert not target.with_name(f"{target.name}-wal").exists()
    assert not target.with_name(f"{target.name}-shm").exists()


def test_failed_atomic_replacement_preserves_old_index_and_cleans_temporary_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacement failures leave the old database queryable and remove sidecars."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "sample.py"
    source.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    index_workspace(workspace)
    source.write_text("def beta():\n    return 2\n", encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("replace blocked")

    monkeypatch.setattr("synapse.core.index_schema.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace blocked"):
        index_workspace(workspace, force=True)

    target = db_path(workspace)
    reopened = SymbolIndex(target)
    assert [symbol.name for symbol in reopened.search_symbols("alpha")] == ["alpha"]
    assert reopened.search_symbols("beta") == []
    assert not any(path.name.startswith(f".{target.stem}.") for path in target.parent.iterdir())


def test_forced_rebuild_cleans_temporary_database_when_initialization_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even schema initialization failures remove the temporary database set."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def fail_index_initialization(_path: Path) -> SymbolIndex:
        raise sqlite3.DatabaseError("schema failed")

    monkeypatch.setattr("synapse.core.indexing.SymbolIndex", fail_index_initialization)
    with pytest.raises(sqlite3.DatabaseError, match="schema failed"):
        index_workspace(workspace, force=True)

    target = db_path(workspace)
    assert not any(path.name.startswith(f".{target.stem}.") for path in target.parent.iterdir())


def test_forced_rebuild_rejects_a_live_workspace_watcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced replacement never races the workspace's active watch writer."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.py").write_text("def alpha(): pass\n", encoding="utf-8")

    with WatchLock(workspace), pytest.raises(WatchAlreadyRunning):
        index_workspace(workspace, force=True)
