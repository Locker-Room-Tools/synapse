"""Incremental watch writes carry handles, in the same transaction as the symbol.

The reproduced failure was a daemon that kept writing under an older persistence
contract. These exercise the real worker, so the assertion is about what lands in the
database, not about what the write path intends.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from synapse.core.index import symbol_handle
from synapse.core.indexing import index_workspace
from synapse.core.watch.worker import WatchWorker
from synapse.core.workspace import db_path

SERVICE = """\
def build_service():
    return 1
"""

HELPERS = """\
def helper():
    return 2
"""


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    (workspace / "app").mkdir(parents=True)
    (workspace / "app" / "service.py").write_text(SERVICE, encoding="utf-8")
    (workspace / "app" / "helpers.py").write_text(HELPERS, encoding="utf-8")
    index_workspace(workspace)
    return workspace


def _persisted(workspace: Path) -> dict[str, str | None]:
    with closing(sqlite3.connect(db_path(workspace))) as connection:
        rows = connection.execute("SELECT id, handle FROM symbols").fetchall()
    return {str(row[0]): (None if row[1] is None else str(row[1])) for row in rows}


def _assert_every_handle_is_derived(workspace: Path) -> dict[str, str | None]:
    persisted = _persisted(workspace)
    assert persisted, "the fixture indexed no symbols"
    assert all(handle == symbol_handle(symbol_id) for symbol_id, handle in persisted.items())
    return persisted


def test_watch_batch_symbols_carry_persisted_handles(tmp_path: Path) -> None:
    """A reindex through the worker persists the derived handle for every new row."""
    workspace = _workspace(tmp_path)
    (workspace / "app" / "service.py").write_text(
        SERVICE + "\n\ndef build_reporter():\n    return 3\n", encoding="utf-8"
    )

    result = WatchWorker(workspace).apply_batch(reindex_paths=["app/service.py"], remove_paths=[])

    assert result.indexed_files == 1
    persisted = _assert_every_handle_is_derived(workspace)
    assert any("build_reporter" in symbol_id for symbol_id in persisted)


def test_repeated_batches_keep_handle_completeness(tmp_path: Path) -> None:
    """Completeness survives repeated incremental churn without another full rebuild."""
    workspace = _workspace(tmp_path)
    worker = WatchWorker(workspace)

    for revision in range(3):
        (workspace / "app" / "helpers.py").write_text(
            HELPERS + f"\n\ndef helper_{revision}():\n    return {revision}\n", encoding="utf-8"
        )
        worker.apply_batch(reindex_paths=["app/helpers.py"], remove_paths=[])
        _assert_every_handle_is_derived(workspace)


def test_a_batch_cannot_commit_a_symbol_without_its_handle(tmp_path: Path) -> None:
    """The invariant is the file's, so a handle-less write cannot be committed at all."""
    workspace = _workspace(tmp_path)

    with (
        closing(sqlite3.connect(db_path(workspace))) as connection,
        pytest.raises(sqlite3.IntegrityError, match="NOT NULL"),
    ):
        connection.execute(
            "INSERT INTO symbols (id, file_id, language, kind, native_kind, name, "
            "qualified_name, file_path, container_id, start_line, end_line, start_byte, "
            "end_byte, signature, source, confidence) "
            "VALUES ('py:x', 'app/service.py', 'python', 'function', 'function_definition', "
            "'x', 'x', 'app/service.py', NULL, 1, 2, 0, 10, NULL, 'tree-sitter', 'high')"
        )
