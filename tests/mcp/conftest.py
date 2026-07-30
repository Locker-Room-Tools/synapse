"""Shared MCP test isolation."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_synapse_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every test's index data out of the user-global Synapse directory.

    Tests that need a specific location still win by calling monkeypatch.setenv
    themselves; this fixture only guarantees a safe default.
    """
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "synapse-data"))
