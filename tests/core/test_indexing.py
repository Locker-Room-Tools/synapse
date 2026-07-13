"""Tests for incremental workspace indexing."""

import sqlite3
from pathlib import Path

import pytest

from synapse.core.index import SymbolIndex
from synapse.core.indexing import index_workspace
from synapse.core.workspace import db_path, read_metadata


def test_index_workspace_tracks_new_unchanged_changed_and_deleted_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incremental indexing updates only the files that changed."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    file_path = workspace_root / "alpha.py"
    file_path.write_text("def alpha():\n    return 1\n", encoding="utf-8")

    first = index_workspace(workspace_root)
    second = index_workspace(workspace_root)
    file_path.write_text("def alpha():\n    return 2\n", encoding="utf-8")
    third = index_workspace(workspace_root)
    file_path.unlink()
    fourth = index_workspace(workspace_root)

    assert (first.indexed_files, first.skipped_files, first.removed_files) == (1, 0, 0)
    assert (second.indexed_files, second.skipped_files, second.removed_files) == (0, 1, 0)
    assert (third.indexed_files, third.skipped_files, third.removed_files) == (1, 0, 0)
    assert (fourth.indexed_files, fourth.skipped_files, fourth.removed_files) == (0, 0, 1)
    assert (second.total_files, second.total_symbols) == (1, 1)
    metadata = read_metadata(workspace_root)
    assert metadata is not None
    assert metadata.languages == []
    assert SymbolIndex(db_path(workspace_root)).search_symbols("alpha") == []


def test_index_workspace_skips_unreadable_files_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One unreadable file (e.g. a dangling symlink) must not abort the run."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "good.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (workspace_root / "dangling.py").symlink_to(workspace_root / "missing.py")

    with pytest.warns(UserWarning, match="Skipping unreadable file dangling.py"):
        stats = index_workspace(workspace_root)

    assert (stats.indexed_files, stats.failed_files) == (1, 1)
    index = SymbolIndex(db_path(workspace_root))
    assert [symbol.name for symbol in index.search_symbols("alpha")] == ["alpha"]


def test_index_workspace_force_reindexes_unchanged_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forced indexing reparses cached files even when hashes are unchanged."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "App.tsx").write_text(
        'import React from "react";\nexport const App = () => <div />;\n',
        encoding="utf-8",
    )

    first = index_workspace(workspace_root)
    second = index_workspace(workspace_root, force=True)

    assert (first.indexed_files, first.skipped_files) == (1, 0)
    assert (second.indexed_files, second.skipped_files) == (1, 0)
    assert second.total_files == 1
    assert second.total_symbols >= 1


def test_index_workspace_force_rebuild_keeps_existing_index_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forced indexing replaces the cache only after a full successful rebuild."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    file_path = workspace_root / "sample.py"
    file_path.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    index_workspace(workspace_root)
    before = SymbolIndex(db_path(workspace_root)).list_indexed_files()[0]
    file_path.write_text("def beta():\n    return 2\n", encoding="utf-8")

    def fail_parse(*_: object, **__: object) -> object:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr("synapse.core.indexing.parse_file", fail_parse)

    with pytest.raises(RuntimeError, match="boom"):
        index_workspace(workspace_root, force=True)

    index = SymbolIndex(db_path(workspace_root))
    after = index.list_indexed_files()[0]
    assert after.content_hash == before.content_hash
    assert [symbol.name for symbol in index.search_symbols("alpha")] == ["alpha"]
    assert index.search_symbols("beta") == []


def test_index_workspace_force_rebuild_uses_one_write_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forced rebuilds avoid reconnecting to SQLite for every indexed file."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "one.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (workspace_root / "two.py").write_text("def two():\n    return 2\n", encoding="utf-8")
    original_connect = SymbolIndex._connect
    connection_count = 0

    def counting_connect(self: SymbolIndex) -> sqlite3.Connection:
        nonlocal connection_count
        connection_count += 1
        if connection_count > 2:
            msg = "force rebuild opened too many SQLite connections"
            raise AssertionError(msg)
        return original_connect(self)

    monkeypatch.setattr(SymbolIndex, "_connect", counting_connect)

    stats = index_workspace(workspace_root, force=True)

    assert stats.indexed_files == 2
    assert connection_count == 2


def test_index_workspace_persists_relations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Indexing stores derived CONTAINS/IMPORTS relations alongside symbols."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "sample.py").write_text(
        "class Example:\n    def method(self):\n        return 1\n",
        encoding="utf-8",
    )

    index_workspace(workspace_root)

    index = SymbolIndex(db_path(workspace_root))
    container = next(
        symbol for symbol in index.search_symbols("Example") if symbol.name == "Example"
    )
    dependencies = index.get_dependencies(container.id)
    assert any(relation.kind == "contains" for relation in dependencies)


def test_index_workspace_resolves_cross_file_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second indexing pass resolves references against the whole workspace."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "caller.py").write_text(
        "def caller():\n    return helper()\n",
        encoding="utf-8",
    )
    (workspace_root / "target.py").write_text(
        "def helper():\n    return 1\n",
        encoding="utf-8",
    )

    index_workspace(workspace_root)

    index = SymbolIndex(db_path(workspace_root))
    helper = next(symbol for symbol in index.search_symbols("helper") if symbol.name == "helper")
    references = index.get_references(helper.id)
    assert len(references) == 1
    assert references[0].from_file_path == "caller.py"
    assert references[0].to_symbol_id == helper.id
