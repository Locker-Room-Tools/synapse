"""Repository-wide test isolation.

Every workspace path resolves to a data directory under `SYNAPSE_DATA_DIR`, which
defaults to the user's own `~/.local/share/synapse`. No test may reach it: these tests
stop daemons, rebuild indexes, and fabricate corrupt database states. A test that needs
a specific location still wins by calling `monkeypatch.setenv` itself.
"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_synapse_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "synapse-data"))
