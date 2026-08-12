"""Tests for reference fingerprinting and staleness detection."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from synapse.core.index import SymbolIndex
from synapse.core.indexing import parser
from synapse.core.indexing import references as references_module
from synapse.core.indexing.references import (
    REFERENCE_FINGERPRINT_KEY,
    reference_extraction_fingerprint,
    reference_index_is_stale,
)
from synapse.core.workspace import db_path


def test_reference_fingerprint_is_deterministic_and_content_sensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fingerprint is stable per content and reacts to extractor version bumps."""
    first = reference_extraction_fingerprint()
    second = reference_extraction_fingerprint()
    assert first == second

    monkeypatch.setattr(parser, "REFERENCE_EXTRACTOR_VERSION", 999)
    monkeypatch.setattr(references_module, "REFERENCE_EXTRACTOR_VERSION", 999)
    assert reference_extraction_fingerprint() != first


def test_reference_index_staleness_probe_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The staleness probe never creates or migrates the database."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    database_path = db_path(workspace_root)

    # Missing database: fresh-build path handles it, nothing is created.
    assert reference_index_is_stale(workspace_root) is False
    assert not database_path.exists()

    # A database without index_meta (pre-fingerprint index) is stale.
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("CREATE TABLE relations (id TEXT PRIMARY KEY)")
    assert reference_index_is_stale(workspace_root) is True
    with closing(sqlite3.connect(database_path)) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "index_meta" not in tables

    # A current fingerprint reads as fresh; any drift reads as stale.
    database_path.unlink()
    index = SymbolIndex(database_path)
    index.set_meta(REFERENCE_FINGERPRINT_KEY, reference_extraction_fingerprint())
    assert reference_index_is_stale(workspace_root) is False
    index.set_meta(REFERENCE_FINGERPRINT_KEY, "stale")
    assert reference_index_is_stale(workspace_root) is True
