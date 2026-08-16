"""Tests for workspace storage helpers."""

from pathlib import Path

import pytest

from synapse.core.workspace import (
    data_dir_path,
    db_path,
    detect_workspace_root,
    logs_dir,
    metadata_path,
    read_metadata,
    watch_journal_path,
    watch_lock_path,
    watch_state_path,
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
    assert watch_state_path(workspace_root).name == "watch.json"
    assert watch_lock_path(workspace_root).name == "watch.lock"
    assert watch_journal_path(workspace_root).name == "watch.journal"


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


def test_data_dir_path_does_not_create_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inspection path helper is safe for status operations."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data-root"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))

    resolved = data_dir_path(workspace)

    assert resolved.parent == data_root / "workspaces"
    assert not data_root.exists()


def test_corrupt_metadata_reads_as_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unparseable or truncated metadata means uninitialized, never a crash."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    write_metadata(workspace_root, last_indexed_at=None, languages=[])

    target = metadata_path(workspace_root)
    for corrupt in ('{"name": "half', "[]", '{"name": "only-a-name"}', ""):
        target.write_text(corrupt, encoding="utf-8")
        assert read_metadata(workspace_root) is None


def test_write_metadata_recovers_from_a_corrupt_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A corrupt metadata file is replaced with fresh metadata, not propagated."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    metadata_path(workspace_root).write_text('{"broken', encoding="utf-8")

    written = write_metadata(workspace_root, last_indexed_at=None, languages=["python"])

    assert read_metadata(workspace_root) == written
    assert written.created_at


def test_metadata_write_leaves_no_temporary_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The atomic write path replaces the file and cleans up its temporary."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))

    write_metadata(workspace_root, last_indexed_at=None, languages=[])

    siblings = sorted(p.name for p in metadata_path(workspace_root).parent.iterdir())
    assert siblings == ["metadata.json"]


def test_detect_workspace_root_prefers_nearest_git_ancestor_over_project_markers(
    tmp_path: Path,
) -> None:
    """Global serve is repository-wide even when a nested package has metadata."""
    repository = tmp_path / "repository"
    nested = repository / "packages" / "child"
    nested.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "packages" / "pyproject.toml").write_text(
        "[project]\nname = 'nested'\n",
        encoding="utf-8",
    )

    assert detect_workspace_root(nested) == repository


def test_detect_workspace_root_without_git_falls_back_to_start_directory(
    tmp_path: Path,
) -> None:
    """Non-Git directories do not inherit an unrelated project marker."""
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    (parent / "package.json").write_text("{}", encoding="utf-8")

    assert detect_workspace_root(child) == child
