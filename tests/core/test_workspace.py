"""Tests for workspace storage helpers."""

from pathlib import Path

import pytest

from synapse.core.workspace import (
    db_path,
    logs_dir,
    metadata_path,
    read_metadata,
    workspace_id,
    write_metadata,
)


def test_workspace_paths_live_under_the_configured_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace paths resolve under the system data root override."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    data_root = tmp_path / "data-root"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))

    assert db_path(workspace_root).parent.parent == data_root / "workspaces"
    assert metadata_path(workspace_root).name == "metadata.json"
    assert logs_dir(workspace_root).name == "logs"


def test_workspace_metadata_round_trips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Metadata persists and reloads deterministically."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))

    metadata = write_metadata(
        workspace_root,
        last_indexed_at="2026-06-16T00:00:00+00:00",
        languages=["python", "csharp"],
    )
    reloaded = read_metadata(workspace_root)

    assert reloaded == metadata
    assert workspace_id(workspace_root) == workspace_id(workspace_root.resolve())