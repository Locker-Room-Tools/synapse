"""Multi-process safety of the fingerprint-forced rebuild.

Unlike the rest of the suite, these tests spawn real interpreters. The behaviour under
test is cross-process contention over one workspace index — a watch lock held by another
PID and a SQLite database opened concurrently — which a monkeypatched `subprocess.Popen`
cannot reproduce.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from synapse.core.index import SymbolIndex
from synapse.core.indexing import (
    REFERENCE_FINGERPRINT_KEY,
    index_workspace,
    reference_extraction_fingerprint,
)
from synapse.core.watch.daemon import wait_for_watch_to_stop
from synapse.core.watch.state import pid_is_running, read_watch_status
from synapse.core.watch.supervisor import request_watch_stop
from synapse.core.workspace import db_path, watch_lock_path

# Generous: these spawn a fresh interpreter that imports tree-sitter grammars.
_PROCESS_TIMEOUT_S = 180.0

_SOURCE = "namespace Sample;\npublic class Thing\n{\n    public int Id { get; set; }\n}\n"


def _run_python(script: str, *, workspace: Path, data_root: Path) -> dict[str, object]:
    """Run one snippet in a fresh interpreter and return its JSON verdict."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=_PROCESS_TIMEOUT_S,
        env={
            "SYNAPSE_DATA_DIR": str(data_root),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
            "SYNAPSE_TEST_WORKSPACE": str(workspace),
        },
        check=False,
    )
    assert completed.returncode == 0, (
        f"child failed ({completed.returncode})\nstdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    return dict(json.loads(completed.stdout.strip().splitlines()[-1]))


def _make_workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "thing.cs").write_text(_SOURCE, encoding="utf-8")
    return workspace, tmp_path / "data-root"


_ENSURE_SCRIPT = """
    import json, os
    from pathlib import Path
    from synapse.core.lifecycle import ensure_workspace

    workspace = Path(os.environ["SYNAPSE_TEST_WORKSPACE"])
    result = ensure_workspace(workspace)
    print(json.dumps({"action": result.action, "index": result.index}))
"""

_QUERY_SCRIPT = """
    import json, os
    from pathlib import Path
    from synapse.core.index import SymbolIndex
    from synapse.core.workspace import db_path

    workspace = Path(os.environ["SYNAPSE_TEST_WORKSPACE"])
    index = SymbolIndex(db_path(workspace))
    names = [symbol.name for symbol in index.search_symbols("Thing")]
    print(json.dumps({"names": names}))
"""


def _stale_the_fingerprint(workspace: Path) -> None:
    """Rewrite the stored fingerprint so the next ensure must force a rebuild."""
    index = SymbolIndex(db_path(workspace))
    index.set_meta(REFERENCE_FINGERPRINT_KEY, "stale-fingerprint")
    assert index.get_meta(REFERENCE_FINGERPRINT_KEY) == "stale-fingerprint"


def _assert_no_orphaned_lock(workspace: Path) -> None:
    """The watch lock may exist, but only while a live daemon owns it.

    `ensure_workspace` leaves a daemon running by design, so the lock file being present
    is correct. What must never happen is a lock left behind by a dead process, which
    would block every future rebuild.
    """
    lock_path = watch_lock_path(workspace)
    if not lock_path.exists():
        return
    owner = lock_path.read_text(encoding="utf-8").strip()
    assert owner.isdigit(), f"lock holds no owner pid: {owner!r}"
    assert pid_is_running(int(owner)), f"stale lock left by dead pid {owner}"


def _stop_daemon(workspace: Path) -> None:
    """Stop any daemon a child process left running, so tests do not leak processes."""
    status = read_watch_status(workspace)
    if status.running and pid_is_running(status.pid):
        request_watch_stop(workspace)
        wait_for_watch_to_stop(workspace)


@pytest.mark.slow
def test_stale_fingerprint_rebuilds_once_and_both_processes_recover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale fingerprint forces exactly one rebuild; other processes still query."""
    workspace, data_root = _make_workspace(tmp_path)
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))

    index_workspace(workspace)
    assert (
        SymbolIndex(db_path(workspace)).get_meta(REFERENCE_FINGERPRINT_KEY)
        == reference_extraction_fingerprint()
    )

    _stale_the_fingerprint(workspace)

    try:
        # A second process observes the staleness and rebuilds.
        first = _run_python(_ENSURE_SCRIPT, workspace=workspace, data_root=data_root)
        assert first["action"] in {"initialized", "repaired"}
        assert (
            SymbolIndex(db_path(workspace)).get_meta(REFERENCE_FINGERPRINT_KEY)
            == reference_extraction_fingerprint()
        )

        # A third process finds the index fresh and does not rebuild again: exactly one
        # forced rebuild happened, not one per process.
        second = _run_python(_ENSURE_SCRIPT, workspace=workspace, data_root=data_root)
        assert second["action"] == "reused"

        # Both processes can still query afterwards.
        for _ in range(2):
            names = _run_python(_QUERY_SCRIPT, workspace=workspace, data_root=data_root)["names"]
            assert isinstance(names, list)
            assert "Thing" in names

        _assert_no_orphaned_lock(workspace)
        # WAL mode is intact: no rollback-journal sidecar was left behind.
        database = db_path(workspace)
        assert not database.with_name(database.name + "-journal").exists()
    finally:
        _stop_daemon(workspace)


@pytest.mark.slow
def test_concurrent_ensure_calls_rebuild_once_and_leave_a_queryable_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two processes racing on a stale fingerprint converge on one healthy index."""
    workspace, data_root = _make_workspace(tmp_path)
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))

    index_workspace(workspace)
    _stale_the_fingerprint(workspace)

    script = textwrap.dedent(_ENSURE_SCRIPT)
    env = {
        "SYNAPSE_DATA_DIR": str(data_root),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        "SYNAPSE_TEST_WORKSPACE": str(workspace),
    }
    processes = [
        subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for _ in range(2)
    ]
    try:
        results = [process.communicate(timeout=_PROCESS_TIMEOUT_S) for process in processes]
        codes = [process.returncode for process in processes]

        # At least one must succeed outright. A loser may lose the lock race, but only
        # with a named, bounded failure — never a corrupt index or an unhandled crash.
        assert 0 in codes, f"no process succeeded: {results}"
        for code, (_, stderr) in zip(codes, results, strict=True):
            if code != 0:
                assert (
                    "WatchAlreadyRunning" in stderr
                    or "database is locked" in stderr
                    or "Watch daemon did not stop" in stderr
                ), stderr

        # Whoever won, the fingerprint is current and the index answers queries.
        assert (
            SymbolIndex(db_path(workspace)).get_meta(REFERENCE_FINGERPRINT_KEY)
            == reference_extraction_fingerprint()
        )
        names = _run_python(_QUERY_SCRIPT, workspace=workspace, data_root=data_root)["names"]
        assert isinstance(names, list)
        assert "Thing" in names
        _assert_no_orphaned_lock(workspace)

        # Recovery is real, not just survival: a later process can ensure again.
        assert _run_python(_ENSURE_SCRIPT, workspace=workspace, data_root=data_root)["action"] in {
            "reused",
            "repaired",
        }
    finally:
        _stop_daemon(workspace)
