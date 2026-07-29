"""Tests for incremental workspace indexing."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest

from synapse.core.index import SymbolIndex
from synapse.core.indexing import (
    REFERENCE_FINGERPRINT_KEY,
    index_workspace,
    reference_extraction_fingerprint,
)
from synapse.core.indexing.crawler import hash_source as calculate_source_hash
from synapse.core.indexing.parser import ParsedSource
from synapse.core.indexing.parser import parse_source as parse_source_bytes
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
    try:
        (workspace_root / "dangling.py").symlink_to(workspace_root / "missing.py")
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")

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

    monkeypatch.setattr("synapse.core.indexing.pipeline.parse_source", fail_parse)

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


def test_incremental_reference_reconciliation_handles_add_rename_and_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unchanged callers follow target additions, renames, and deletions."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    caller = workspace / "caller.py"
    target = workspace / "target.py"
    caller.write_text("def caller():\n    return helper()\n", encoding="utf-8")

    index_workspace(workspace)
    index = SymbolIndex(db_path(workspace))
    assert len(index.get_references_by_name("helper")) == 1

    target.write_text("def helper():\n    return 1\n", encoding="utf-8")
    index_workspace(workspace)
    index = SymbolIndex(db_path(workspace))
    helper = index.get_definition("helper")[0]
    assert len(index.get_references(helper.id)) == 1
    assert index.get_references_by_name("helper") == []

    target.write_text("def renamed():\n    return 1\n", encoding="utf-8")
    index_workspace(workspace)
    index = SymbolIndex(db_path(workspace))
    assert index.get_definition("helper") == []
    assert len(index.get_references_by_name("helper")) == 1

    caller.write_text("def caller():\n    return renamed()\n", encoding="utf-8")
    index_workspace(workspace)
    index = SymbolIndex(db_path(workspace))
    renamed = index.get_definition("renamed")[0]
    assert len(index.get_references(renamed.id)) == 1

    target.unlink()
    index_workspace(workspace)
    index = SymbolIndex(db_path(workspace))
    assert index.get_definition("renamed") == []
    assert len(index.get_references_by_name("renamed")) == 1


def test_changed_file_is_read_hashed_and_parsed_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incremental indexing reuses one byte buffer and one syntax tree."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "sample.py"
    source.write_text("def alpha():\n    return 1\n", encoding="utf-8")
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
    monkeypatch.setattr("synapse.core.indexing.pipeline.hash_source", counting_hash_source)
    monkeypatch.setattr("synapse.core.indexing.pipeline.parse_source", counting_parse_source)

    index_workspace(workspace)

    assert calls == {"read": 1, "hash": 1, "parse": 1}


def test_file_symlink_uses_its_lexical_workspace_path_everywhere(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stored files, symbols, IDs, and relations never expose a symlink target path."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external.py"
    external.write_text(
        "def linked():\n    return linked()\n",
        encoding="utf-8",
    )
    link = workspace / "linked.py"
    try:
        link.symlink_to(external)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")

    index_workspace(workspace)

    index = SymbolIndex(db_path(workspace))
    indexed_file = index.list_indexed_files()[0]
    symbol = index.get_definition("linked")[0]
    relations = index.get_dependencies(symbol.id)
    assert indexed_file.path == "linked.py"
    assert symbol.file_path == "linked.py"
    assert ":linked.py:" in symbol.id
    assert all(relation.from_file_path == "linked.py" for relation in relations)
    assert str(external) not in repr((indexed_file, symbol, relations))


def test_missing_workspace_fails_before_cache_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace validation runs before database and metadata path allocation."""
    data_root = tmp_path / "data-root"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))
    missing = tmp_path / "missing"

    with pytest.raises(NotADirectoryError, match="Workspace is not a directory"):
        index_workspace(missing)

    assert not data_root.exists()


def test_index_writes_reference_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every successful index run stamps the current extraction fingerprint."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "alpha.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    index_workspace(workspace_root)

    index = SymbolIndex(db_path(workspace_root))
    assert index.get_meta(REFERENCE_FINGERPRINT_KEY) == reference_extraction_fingerprint()


def test_fingerprint_change_invalidates_stale_relations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fingerprint mismatch escalates a plain reindex to a full forced rebuild."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "alpha.py").write_text(
        "def target():\n    return 1\n\ndef caller():\n    return target()\n",
        encoding="utf-8",
    )

    first = index_workspace(workspace_root)
    assert first.indexed_files == 1

    # Unchanged fingerprint and files: nothing is reindexed.
    unchanged = index_workspace(workspace_root)
    assert (unchanged.indexed_files, unchanged.skipped_files) == (0, 1)

    with closing(sqlite3.connect(db_path(workspace_root))) as connection, connection:
        connection.execute(
            "UPDATE index_meta SET value = 'stale' WHERE key = ?",
            (REFERENCE_FINGERPRINT_KEY,),
        )

    rebuilt = index_workspace(workspace_root)

    # The stale index is fully rebuilt without --force and re-stamped.
    assert rebuilt.indexed_files == 1
    assert rebuilt.skipped_files == 0
    index = SymbolIndex(db_path(workspace_root))
    assert index.get_meta(REFERENCE_FINGERPRINT_KEY) == reference_extraction_fingerprint()
    # The rebuilt relations carry the current extraction semantics.
    rebuilt_references = index.find_references(name="target")
    assert cast(dict[str, object], rebuilt_references["page"])["total"] == 1
    items = rebuilt_references["items"]
    assert isinstance(items, list)
    assert items[0]["match"] == "heuristic"
