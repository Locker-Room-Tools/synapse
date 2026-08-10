"""Tests for lazy workspace initialization and readiness."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from synapse.core import lifecycle
from synapse.core.config import ignore_presets
from synapse.core.indexing import IndexStats
from synapse.core.lifecycle import WorkspaceNotReadyError, WorkspaceState
from synapse.core.watch.state import WatchStatus
from synapse.core.workspace import workspace_id


def _watch_status(
    workspace: Path,
    *,
    running: bool,
    degraded: bool = False,
    pid: int | None = None,
) -> WatchStatus:
    return WatchStatus(
        workspace_path=str(workspace),
        workspace_id=workspace_id(workspace),
        running=running,
        backend="polling" if running else "none",
        degraded=degraded,
        pending=0,
        pid=pid,
        started_at=None,
        stopped_at=None,
        last_event_ts=None,
        last_full_sweep_ts=None,
        last_reconcile_started_at=None,
        last_reconcile_finished_at=None,
        errors_count=0,
        errors=[],
    )


def _watch_payload(
    workspace: Path,
    *,
    running: bool,
    degraded: bool = False,
) -> dict[str, object]:
    return {
        "workspace_path": str(workspace),
        "running": running,
        "degraded": degraded,
    }


@pytest.mark.parametrize(
    ("initialized", "running", "degraded", "expected"),
    [
        (False, False, False, WorkspaceState.UNINITIALIZED),
        (False, True, False, WorkspaceState.INITIALIZING),
        (True, True, False, WorkspaceState.READY),
        (True, False, False, WorkspaceState.DEGRADED),
        (True, True, True, WorkspaceState.DEGRADED),
    ],
)
def test_workspace_status_classifies_lifecycle_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initialized: bool,
    running: bool,
    degraded: bool,
    expected: WorkspaceState,
) -> None:
    """Metadata and daemon health map to the public four-state lifecycle."""
    monkeypatch.setattr(
        lifecycle,
        "read_metadata",
        lambda path: SimpleNamespace() if initialized else None,
    )
    monkeypatch.setattr(
        lifecycle,
        "watch_status_payload",
        lambda path: _watch_payload(tmp_path, running=running, degraded=degraded),
    )

    payload = lifecycle.workspace_status_payload(tmp_path)

    assert payload["workspace_path"] == str(tmp_path)
    assert payload["state"] == expected.value
    assert payload["initialized"] is initialized


def test_uninitialized_status_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inspecting a new workspace never allocates its cache directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data-root"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))

    payload = lifecycle.workspace_status_payload(workspace)

    assert payload["state"] == WorkspaceState.UNINITIALIZED
    assert payload["initialized"] is False
    assert not data_root.exists()


def test_require_workspace_ready_guides_agent_to_ensure_without_creating_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-init queries fail with an actionable bootstrap instruction."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data-root"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))

    with pytest.raises(
        WorkspaceNotReadyError,
        match=r"uninitialized.*synapse_ensure_workspace",
    ):
        lifecycle.require_workspace_ready(workspace)

    assert not data_root.exists()


def test_ensure_workspace_initializes_missing_grammars_index_and_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first ensure performs every required initialization step once."""
    calls: list[object] = []
    stopped = _watch_status(tmp_path, running=False)
    healthy = _watch_status(tmp_path, running=True, pid=1234)
    monkeypatch.setattr(lifecycle, "read_metadata", lambda path: None)
    monkeypatch.setattr(lifecycle, "missing_grammars", lambda: ("python",))

    def fake_install_grammars() -> tuple[str, ...]:
        calls.append("grammars")
        return ("python",)

    def fake_index(path: Path, *, force: bool = False) -> IndexStats:
        calls.append(("index", path, force))
        return IndexStats(str(path), 1, 0, 0, 0, 1, 2, ["python"])

    def fake_ensure_daemon(path: Path) -> WatchStatus:
        calls.append(("daemon", path))
        return healthy

    monkeypatch.setattr(lifecycle, "install_grammars", fake_install_grammars)
    monkeypatch.setattr(lifecycle, "read_watch_status", lambda path: stopped)
    monkeypatch.setattr(lifecycle, "index_workspace", fake_index)
    monkeypatch.setattr(lifecycle, "ensure_watch_daemon", fake_ensure_daemon)

    result = lifecycle.ensure_workspace(tmp_path)

    assert result.action == "initialized"
    assert result.initialized is True
    assert result.index == {"files": 1, "symbols": 2, "languages": ["python"]}
    assert result.daemon["running"] is True
    assert calls == [
        "grammars",
        ("index", tmp_path, False),
        ("daemon", tmp_path),
    ]


def test_ensure_workspace_reuses_healthy_index_and_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy initialized workspace avoids downloads, indexing, and restarts."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    healthy = _watch_status(tmp_path, running=True, pid=1234)
    monkeypatch.setattr(lifecycle, "read_metadata", lambda path: SimpleNamespace())
    monkeypatch.setattr(lifecycle, "missing_grammars", lambda: ())
    monkeypatch.setattr(lifecycle, "read_watch_status", lambda path: healthy)
    monkeypatch.setattr(lifecycle, "pid_is_running", lambda pid: True)
    monkeypatch.setattr(
        lifecycle,
        "install_grammars",
        lambda: pytest.fail("downloaded grammars"),
    )
    monkeypatch.setattr(
        lifecycle,
        "index_workspace",
        lambda *args, **kwargs: pytest.fail("reindexed"),
    )

    class FakeIndex:
        def __init__(self, path: Path) -> None:
            self.path = path

        def workspace_stats(self) -> dict[str, object]:
            return {"files": 3, "symbols": 8, "languages": ["python"]}

    monkeypatch.setattr(lifecycle, "SymbolIndex", FakeIndex)
    monkeypatch.setattr(lifecycle, "ensure_watch_daemon", lambda path: healthy)

    result = lifecycle.ensure_workspace(tmp_path)

    assert result.action == "reused"
    assert result.index == {"files": 3, "symbols": 8, "languages": ["python"]}


def test_ensure_workspace_repairs_dead_daemon_without_reindexing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing index data is reused while a dead daemon is recovered."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    stopped = _watch_status(tmp_path, running=False)
    healthy = _watch_status(tmp_path, running=True, pid=4321)
    monkeypatch.setattr(lifecycle, "read_metadata", lambda path: SimpleNamespace())
    monkeypatch.setattr(lifecycle, "missing_grammars", lambda: ())
    monkeypatch.setattr(lifecycle, "read_watch_status", lambda path: stopped)
    monkeypatch.setattr(
        lifecycle,
        "index_workspace",
        lambda *args, **kwargs: pytest.fail("reindexed healthy index"),
    )

    class FakeIndex:
        def __init__(self, path: Path) -> None:
            self.path = path

        def workspace_stats(self) -> dict[str, object]:
            return {"files": 1, "symbols": 2, "languages": ["rust"]}

    monkeypatch.setattr(lifecycle, "SymbolIndex", FakeIndex)
    monkeypatch.setattr(lifecycle, "ensure_watch_daemon", lambda path: healthy)

    result = lifecycle.ensure_workspace(tmp_path)

    assert result.action == "repaired"
    assert result.daemon["pid"] == 4321


def test_ensure_workspace_offline_fails_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offline initialization never downloads, indexes, or starts a daemon."""
    monkeypatch.setattr(lifecycle, "read_metadata", lambda path: None)
    monkeypatch.setattr(lifecycle, "missing_grammars", lambda: ("python",))
    monkeypatch.setattr(
        lifecycle,
        "install_grammars",
        lambda: pytest.fail("downloaded grammars"),
    )
    monkeypatch.setattr(
        lifecycle,
        "index_workspace",
        lambda *args, **kwargs: pytest.fail("indexed"),
    )
    monkeypatch.setattr(
        lifecycle,
        "ensure_watch_daemon",
        lambda path: pytest.fail("started daemon"),
    )

    with pytest.raises(ValueError, match="offline mode"):
        lifecycle.ensure_workspace(tmp_path, offline=True)


def test_ensure_workspace_forces_reindex_on_stale_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale reference fingerprint stops the daemon, then forces a full rebuild."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    calls: list[object] = []
    healthy = _watch_status(tmp_path, running=True, pid=1234)
    monkeypatch.setattr(lifecycle, "read_metadata", lambda path: SimpleNamespace())
    monkeypatch.setattr(lifecycle, "missing_grammars", lambda: ())
    monkeypatch.setattr(lifecycle, "read_watch_status", lambda path: healthy)
    monkeypatch.setattr(lifecycle, "pid_is_running", lambda pid: True)
    monkeypatch.setattr(lifecycle, "reference_index_is_stale", lambda root: True)

    def fake_request_stop(path: Path) -> None:
        calls.append("stop-daemon")

    def fake_wait_stop(path: Path) -> bool:
        calls.append("wait-stop")
        return True

    def fake_index(path: Path, *, force: bool = False) -> IndexStats:
        calls.append(("index", force))
        return IndexStats(str(path), 2, 0, 0, 0, 2, 5, ["csharp"])

    def fake_ensure_daemon(path: Path) -> WatchStatus:
        calls.append("ensure-daemon")
        return healthy

    monkeypatch.setattr(lifecycle, "request_watch_stop", fake_request_stop)
    monkeypatch.setattr(lifecycle, "wait_for_watch_to_stop", fake_wait_stop)
    monkeypatch.setattr(lifecycle, "index_workspace", fake_index)
    monkeypatch.setattr(lifecycle, "ensure_watch_daemon", fake_ensure_daemon)

    result = lifecycle.ensure_workspace(tmp_path)

    # The daemon stops before the forced rebuild takes the watch lock.
    assert calls == ["stop-daemon", "wait-stop", ("index", True), "ensure-daemon"]
    assert result.action == "repaired"
    assert result.index == {"files": 2, "symbols": 5, "languages": ["csharp"]}


def _stub_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    order: list[str] | None = None,
) -> None:
    """Stub a first-run ensure_workspace down to just the bootstrap decision."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(lifecycle, "read_metadata", lambda path: None)
    monkeypatch.setattr(lifecycle, "missing_grammars", lambda: ())
    monkeypatch.setattr(
        lifecycle, "read_watch_status", lambda path: _watch_status(path, running=False)
    )
    monkeypatch.setattr(
        lifecycle, "ensure_watch_daemon", lambda path: _watch_status(path, running=True, pid=1)
    )

    def fake_index(path: Path, *, force: bool = False) -> IndexStats:
        if order is not None:
            order.append("index")
        return IndexStats(str(path), 1, 0, 0, 0, 1, 2, ["python"])

    monkeypatch.setattr(lifecycle, "index_workspace", fake_index)


def test_first_init_creates_a_synapseignore_from_detected_ecosystems(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recognizable workspace gets ignore rules before its very first crawl."""
    order: list[str] = []
    _stub_initialization(tmp_path, monkeypatch, order)
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")

    result = lifecycle.ensure_workspace(tmp_path)

    ignore_file = tmp_path / ".synapseignore"
    assert ignore_file.exists()
    assert "*.min.js" in ignore_file.read_text(encoding="utf-8")
    assert result.ignore_bootstrap == {"path": str(ignore_file), "patterns": 7}
    # The file must exist before the first index, or the first crawl ignores nothing.
    assert order == ["index"]


def test_first_init_never_overwrites_an_existing_synapseignore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the file exists it belongs to the repository, not to Synapse."""
    _stub_initialization(tmp_path, monkeypatch)
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / ".synapseignore").write_text("# mine\n", encoding="utf-8")

    result = lifecycle.ensure_workspace(tmp_path)

    assert (tmp_path / ".synapseignore").read_text(encoding="utf-8") == "# mine\n"
    assert result.ignore_bootstrap is None


def test_first_init_writes_nothing_when_no_ecosystem_is_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognizable workspace gets no file rather than an empty one."""
    _stub_initialization(tmp_path, monkeypatch)

    result = lifecycle.ensure_workspace(tmp_path)

    assert not (tmp_path / ".synapseignore").exists()
    assert result.ignore_bootstrap is None


@pytest.mark.parametrize("opt_out", ["env", "config"])
def test_first_init_honors_the_bootstrap_opt_out(
    opt_out: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both opt-outs suppress the write without affecting anything else."""
    _stub_initialization(tmp_path, monkeypatch)
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    if opt_out == "env":
        monkeypatch.setenv("SYNAPSE_NO_IGNORE_BOOTSTRAP", "1")
    else:
        config_path = tmp_path / ".synapse" / "config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text('{"auto_ignore_bootstrap": false}', encoding="utf-8")

    result = lifecycle.ensure_workspace(tmp_path)

    assert not (tmp_path / ".synapseignore").exists()
    assert result.ignore_bootstrap is None


def test_bootstrap_failure_warns_but_still_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read-only checkout must still be indexable; the bootstrap is best-effort."""
    _stub_initialization(tmp_path, monkeypatch)
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(ignore_presets, "write_ignore_file", _raise_permission_error)

    with pytest.warns(UserWarning, match="Could not create"):
        result = lifecycle.ensure_workspace(tmp_path)

    assert result.action == "initialized"
    assert result.ignore_bootstrap is None


def _raise_permission_error(*args: object, **kwargs: object) -> None:
    raise PermissionError("read-only file system")


def test_repair_does_not_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a first-run initialization may write into the repository."""
    _stub_initialization(tmp_path, monkeypatch)
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(lifecycle, "read_metadata", lambda path: {"initialized": True})

    result = lifecycle.ensure_workspace(tmp_path, force=True)

    assert result.action == "repaired"
    assert not (tmp_path / ".synapseignore").exists()
    assert result.ignore_bootstrap is None
