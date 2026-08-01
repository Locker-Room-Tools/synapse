"""Navigation readiness: queryable AND semantically current, repaired only when not.

`WorkspaceState.READY` covers metadata and daemon health only. Because the reference
fingerprint includes the schema version, a workspace can migrate its SQLite schema and
keep relations produced under older extraction semantics, so a READY workspace can serve
stale evidence indefinitely. These tests pin the complete decision and its cost.
"""

from pathlib import Path

import pytest

from synapse.core import lifecycle
from synapse.core.lifecycle import (
    WorkspaceNotReadyError,
    ensure_navigation_ready,
    navigation_repair_reason,
)
from synapse.core.watch.supervisor import WatchAlreadyRunning


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "synapse-data"))


def _healthy(
    monkeypatch: pytest.MonkeyPatch,
    *,
    db_exists: bool = True,
    state: str = "ready",
    missing: tuple[str, ...] = (),
    stale: bool = False,
) -> None:
    """Pin every readiness probe so a test states exactly one condition."""
    monkeypatch.setattr(
        lifecycle, "db_file_path", lambda root: root / ("index.sqlite" if db_exists else "absent")
    )
    monkeypatch.setattr(lifecycle, "workspace_status_payload", lambda root: {"state": state})
    monkeypatch.setattr(lifecycle, "missing_grammars", lambda: missing)
    monkeypatch.setattr(lifecycle, "reference_index_is_stale", lambda root: stale)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "index.sqlite").write_bytes(b"")
    return workspace


def _forbid_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*_: object, **__: object) -> object:
        raise AssertionError("a healthy, current workspace must not be ensured or indexed")

    monkeypatch.setattr(lifecycle, "ensure_workspace", _fail)


def test_ready_and_current_workspace_is_never_ensured_or_reindexed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The healthy path pays four cheap probes and no writes."""
    workspace = _workspace(tmp_path)
    _healthy(monkeypatch)
    _forbid_repair(monkeypatch)

    assert navigation_repair_reason(workspace) is None
    assert ensure_navigation_ready(workspace) == workspace


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"db_exists": False}, "no-index"),
        ({"state": "uninitialized"}, "not-ready"),
        ({"state": "degraded"}, "not-ready"),
        ({"missing": ("python",)}, "missing-grammars"),
        ({"stale": True}, "stale-references"),
    ],
)
def test_every_repair_trigger_is_named(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, object],
    expected: str,
) -> None:
    """Each condition that must force a repair reports its own reason id."""
    workspace = _workspace(tmp_path)
    _healthy(monkeypatch, **kwargs)  # type: ignore[arg-type]

    assert navigation_repair_reason(workspace) == expected


def test_stale_fingerprint_on_a_ready_daemon_repairs_before_navigating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact regression: READY daemon, migrated schema, stale extraction semantics."""
    workspace = _workspace(tmp_path)
    stale = {"value": True}
    ensured: list[Path] = []

    monkeypatch.setattr(lifecycle, "db_file_path", lambda root: root / "index.sqlite")
    monkeypatch.setattr(lifecycle, "workspace_status_payload", lambda root: {"state": "ready"})
    monkeypatch.setattr(lifecycle, "missing_grammars", lambda: ())
    monkeypatch.setattr(lifecycle, "reference_index_is_stale", lambda root: stale["value"])

    def fake_ensure(root: Path) -> None:
        ensured.append(root)
        stale["value"] = False

    monkeypatch.setattr(lifecycle, "ensure_workspace", fake_ensure)

    assert ensure_navigation_ready(workspace) == workspace
    assert ensured == [workspace]
    # Now current: a second navigation call must not rebuild again.
    _forbid_repair(monkeypatch)
    assert ensure_navigation_ready(workspace) == workspace


def test_missing_grammar_on_a_ready_daemon_repairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A READY workspace missing a parser cannot answer for that language."""
    workspace = _workspace(tmp_path)
    missing: dict[str, tuple[str, ...]] = {"value": ("csharp",)}
    ensured: list[Path] = []

    monkeypatch.setattr(lifecycle, "db_file_path", lambda root: root / "index.sqlite")
    monkeypatch.setattr(lifecycle, "workspace_status_payload", lambda root: {"state": "ready"})
    monkeypatch.setattr(lifecycle, "missing_grammars", lambda: missing["value"])
    monkeypatch.setattr(lifecycle, "reference_index_is_stale", lambda root: False)

    def fake_ensure(root: Path) -> None:
        ensured.append(root)
        missing["value"] = ()

    monkeypatch.setattr(lifecycle, "ensure_workspace", fake_ensure)

    assert ensure_navigation_ready(workspace) == workspace
    assert ensured == [workspace]


def test_degraded_workspace_still_initializes_lazily(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lazy initialization of an uninitialized or degraded workspace is unchanged."""
    workspace = _workspace(tmp_path)
    state = {"value": "uninitialized"}
    ensured: list[Path] = []

    monkeypatch.setattr(lifecycle, "db_file_path", lambda root: root / "index.sqlite")
    monkeypatch.setattr(
        lifecycle, "workspace_status_payload", lambda root: {"state": state["value"]}
    )
    monkeypatch.setattr(lifecycle, "missing_grammars", lambda: ())
    monkeypatch.setattr(lifecycle, "reference_index_is_stale", lambda root: False)

    def fake_ensure(root: Path) -> None:
        ensured.append(root)
        state["value"] = "ready"

    monkeypatch.setattr(lifecycle, "ensure_workspace", fake_ensure)

    assert ensure_navigation_ready(workspace) == workspace
    assert ensured == [workspace]


def test_lost_repair_race_converges_on_the_winners_fresh_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Losing the watch lock is not an error when the winner left a current index."""
    workspace = _workspace(tmp_path)
    stale = {"value": True}

    monkeypatch.setattr(lifecycle, "db_file_path", lambda root: root / "index.sqlite")
    monkeypatch.setattr(lifecycle, "workspace_status_payload", lambda root: {"state": "ready"})
    monkeypatch.setattr(lifecycle, "missing_grammars", lambda: ())
    monkeypatch.setattr(lifecycle, "reference_index_is_stale", lambda root: stale["value"])

    def losing_ensure(root: Path) -> None:
        # The concurrent winner finishes its atomic rebuild while we are blocked.
        stale["value"] = False
        raise WatchAlreadyRunning(f"watch daemon already running for {root} (pid 1)")

    monkeypatch.setattr(lifecycle, "ensure_workspace", losing_ensure)

    assert ensure_navigation_ready(workspace) == workspace


def test_lost_repair_race_with_a_still_stale_index_fails_by_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A race that leaves the index stale must not be presented as a working workspace."""
    workspace = _workspace(tmp_path)
    _healthy(monkeypatch, stale=True)
    # The wait is bounded; shorten it so the failure path stays a fast test.
    monkeypatch.setattr(lifecycle, "NAVIGATION_REPAIR_TIMEOUT_S", 0.05)
    monkeypatch.setattr(lifecycle, "_NAVIGATION_POLL_INTERVAL_S", 0.01)

    def losing_ensure(root: Path) -> None:
        raise WatchAlreadyRunning(f"watch daemon already running for {root} (pid 1)")

    monkeypatch.setattr(lifecycle, "ensure_workspace", losing_ensure)

    with pytest.raises(WorkspaceNotReadyError, match="did not finish"):
        ensure_navigation_ready(workspace)


def test_repair_that_does_not_restore_freshness_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repair is only believed when the workspace is actually queryable afterwards."""
    workspace = _workspace(tmp_path)
    _healthy(monkeypatch, stale=True)
    monkeypatch.setattr(lifecycle, "ensure_workspace", lambda root: None)

    with pytest.raises(WorkspaceNotReadyError, match="still stale-references"):
        ensure_navigation_ready(workspace)


def test_readiness_probe_never_creates_workspace_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe runs while a daemon may own the database, so it must not write."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "probe-data"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))

    assert navigation_repair_reason(workspace) == "no-index"
    assert not data_root.exists()


def test_concurrent_repair_wait_is_bounded_and_polls_until_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lost race waits for the winner rather than failing on a lock it never caused."""
    workspace = _workspace(tmp_path)
    remaining = {"polls": 3}

    monkeypatch.setattr(lifecycle, "db_file_path", lambda root: root / "index.sqlite")
    monkeypatch.setattr(lifecycle, "missing_grammars", lambda: ())
    monkeypatch.setattr(lifecycle, "reference_index_is_stale", lambda root: False)
    monkeypatch.setattr(lifecycle, "NAVIGATION_REPAIR_TIMEOUT_S", 5.0)
    monkeypatch.setattr(lifecycle, "_NAVIGATION_POLL_INTERVAL_S", 0.01)

    def status(root: Path) -> dict[str, object]:
        # The winner has stopped the daemon to take the watch lock, so the workspace
        # reads as not-ready until its atomic swap lands.
        if remaining["polls"] > 0:
            remaining["polls"] -= 1
            return {"state": "degraded"}
        return {"state": "ready"}

    monkeypatch.setattr(lifecycle, "workspace_status_payload", status)

    def losing_ensure(root: Path) -> None:
        raise WatchAlreadyRunning(f"watch daemon already running for {root} (pid 1)")

    monkeypatch.setattr(lifecycle, "ensure_workspace", losing_ensure)

    assert ensure_navigation_ready(workspace) == workspace
    assert remaining["polls"] == 0
