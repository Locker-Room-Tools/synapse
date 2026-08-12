"""The acceptance contract: every handle orientation renders, inspection resolves.

These run against a real indexed workspace and a real incremental watch batch, because
the defect this covers was invisible to fixtures built from the current write path: it
only appeared once a second writer touched an already-indexed file.
"""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from synapse.core.index import SymbolIndex, symbol_handle
from synapse.core.indexing import index_workspace
from synapse.core.navigation import (
    InspectRequest,
    OrientRequest,
    inspect_symbols,
    orient_workspace,
)
from synapse.core.watch.reconcile import reconcile_workspace
from synapse.core.workspace import db_path
from tests.core.index.legacy_databases import INVALID_ALPHABET_HANDLE, foreign_handle

# The pre-6 symbols table: nullable, unconstrained handle column.
V4_SCHEMA_SYMBOLS_ONLY = """
CREATE TABLE symbols (
    id TEXT PRIMARY KEY, file_id TEXT NOT NULL, language TEXT NOT NULL, kind TEXT NOT NULL,
    native_kind TEXT NOT NULL, name TEXT NOT NULL, qualified_name TEXT, file_path TEXT NOT NULL,
    container_id TEXT, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
    start_byte INTEGER NOT NULL, end_byte INTEGER NOT NULL, signature TEXT,
    source TEXT NOT NULL, confidence TEXT NOT NULL, handle TEXT
)
"""

SERVICE = """\
class OrderService:
    def submit_order(self, order):
        return validate_order(order)

    def cancel_order(self, order):
        return order
"""

VALIDATION = """\
def validate_order(order):
    return bool(order)


def reject_order(order):
    return not validate_order(order)
"""

REPORTING = """\
def build_report(orders):
    return len(orders)
"""


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    (workspace / "app").mkdir(parents=True)
    (workspace / "app" / "service.py").write_text(SERVICE, encoding="utf-8")
    (workspace / "app" / "validation.py").write_text(VALIDATION, encoding="utf-8")
    (workspace / "app" / "reporting.py").write_text(REPORTING, encoding="utf-8")
    index_workspace(workspace)
    return workspace


def _orient(workspace: Path, terms: list[str]) -> dict[str, Any]:
    index = SymbolIndex(db_path(workspace))
    raw = orient_workspace(index, OrientRequest(terms=tuple(terms)), workspace_root=workspace)
    return _payload(raw)


def _inspect(workspace: Path, handles: list[str]) -> dict[str, Any]:
    index = SymbolIndex(db_path(workspace))
    raw = inspect_symbols(index, InspectRequest(symbols=tuple(handles)), workspace_root=workspace)
    return _payload(raw)


def _payload(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def _missing(inspected: dict[str, Any]) -> list[str]:
    """`missing` is omitted entirely when nothing is missing."""
    return [str(handle) for handle in inspected.get("missing", [])]


def _resolved(inspected: dict[str, Any]) -> list[str]:
    return [str(entry["h"]) for entry in inspected.get("symbols", [])]


def _rendered_handles(payload: dict[str, Any]) -> list[str]:
    """Every handle orientation put on the wire, from both channels that emit one."""
    handles = [str(match["h"]) for match in payload.get("matches", []) if match.get("h")]
    handles.extend(
        str(entry["h"]) for entry in payload.get("map_entrypoints", []) if entry.get("h")
    )
    return handles


def _null_handles(workspace: Path) -> int:
    with closing(sqlite3.connect(db_path(workspace))) as connection:
        row = connection.execute("SELECT COUNT(*) FROM symbols WHERE handle IS NULL").fetchone()
    return int(row[0])


def test_orient_handles_round_trip_through_one_inspection(tmp_path: Path) -> None:
    """Handles rendered for indexed declarations resolve with an empty missing list."""
    workspace = _workspace(tmp_path)

    oriented = _orient(workspace, ["OrderService", "validate_order"])
    handles = _rendered_handles(oriented)
    assert handles, "orientation returned no handles to verify"

    inspected = _inspect(workspace, handles[:8])

    assert _missing(inspected) == []
    assert set(_resolved(inspected)) == set(handles[:8])


def test_handles_round_trip_after_an_incremental_update(tmp_path: Path) -> None:
    """A watch batch must leave both the changed and the untouched files resolvable."""
    workspace = _workspace(tmp_path)
    before = _orient(workspace, ["build_report"])
    unchanged = _rendered_handles(before)[:1]
    assert unchanged

    (workspace / "app" / "validation.py").write_text(
        VALIDATION + "\n\ndef approve_order(order):\n    return validate_order(order)\n",
        encoding="utf-8",
    )
    reconcile_workspace(workspace)

    assert _null_handles(workspace) == 0

    changed = _rendered_handles(_orient(workspace, ["approve_order"]))
    assert changed

    inspected = _inspect(workspace, [*changed[:2], *unchanged])

    assert _missing(inspected) == []
    assert set(_resolved(inspected)) == {*changed[:2], *unchanged}


def test_an_unknown_handle_stays_honestly_missing(tmp_path: Path) -> None:
    """A well-formed handle for nothing indexed is reported, never substituted."""
    workspace = _workspace(tmp_path)
    known = _rendered_handles(_orient(workspace, ["validate_order"]))[:1]
    unknown = symbol_handle("python:app/absent.py:function:never_indexed:1")

    inspected = _inspect(workspace, [*known, unknown])

    assert _missing(inspected) == [unknown]
    assert _resolved(inspected) == known


def test_a_removed_declaration_is_reported_missing_rather_than_guessed(tmp_path: Path) -> None:
    """A deletion race between the two calls stays a deletion, not a nearby match."""
    workspace = _workspace(tmp_path)
    doomed = _rendered_handles(_orient(workspace, ["build_report"]))[:1]
    assert doomed

    (workspace / "app" / "reporting.py").unlink()
    reconcile_workspace(workspace)

    inspected = _inspect(workspace, doomed)

    assert _missing(inspected) == doomed
    assert _resolved(inspected) == []


def _corrupt_handles_to_legacy_state(workspace: Path) -> None:
    """Rewrite the index into the pre-6 state, including both legacy counterexamples.

    Reproduces what an upgrade actually meets: some rows never had a handle, and some
    carry text that a format test would wave through -- invalid characters at the right
    length, or a perfectly well-formed handle belonging to a different symbol.
    """
    target = db_path(workspace)
    with closing(sqlite3.connect(target)) as connection, connection:
        rows = connection.execute("SELECT id FROM symbols ORDER BY id").fetchall()
        ids = [str(row[0]) for row in rows]
        # The constraint has to go before the bad values can be stored at all.
        connection.execute("DROP TABLE IF EXISTS symbols_legacy")
        connection.execute("ALTER TABLE symbols RENAME TO symbols_legacy")
        connection.execute(V4_SCHEMA_SYMBOLS_ONLY)
        connection.execute(
            "INSERT INTO symbols SELECT id, file_id, language, kind, native_kind, name, "
            "qualified_name, file_path, container_id, start_line, end_line, start_byte, "
            "end_byte, signature, source, confidence, handle FROM symbols_legacy"
        )
        connection.execute("DROP TABLE symbols_legacy")
        connection.execute("UPDATE symbols SET handle = NULL")
        if ids:
            connection.execute(
                "UPDATE symbols SET handle = ? WHERE id = ?", (INVALID_ALPHABET_HANDLE, ids[0])
            )
        if len(ids) > 1:
            connection.execute(
                "UPDATE symbols SET handle = ? WHERE id = ?",
                (foreign_handle("python:nowhere.py:function:absent:1"), ids[1]),
            )
        connection.execute("PRAGMA user_version = 5")


def test_round_trip_is_restored_after_migrating_a_legacy_index(tmp_path: Path) -> None:
    """The public contract holds through an upgrade, not only on a freshly built index."""
    workspace = _workspace(tmp_path)
    _corrupt_handles_to_legacy_state(workspace)

    # The migration an upgrade performs: opening the index is what normalizes handles.
    SymbolIndex(db_path(workspace))

    assert _null_handles(workspace) == 0
    handles = _rendered_handles(_orient(workspace, ["OrderService", "validate_order"]))
    assert handles
    inspected = _inspect(workspace, handles[:8])
    assert _missing(inspected) == []
    assert set(_resolved(inspected)) == set(handles[:8])


def test_round_trip_survives_an_incremental_update_after_migration(tmp_path: Path) -> None:
    """Migration must not leave the workspace needing another rebuild to stay correct."""
    workspace = _workspace(tmp_path)
    _corrupt_handles_to_legacy_state(workspace)
    SymbolIndex(db_path(workspace))

    (workspace / "app" / "validation.py").write_text(
        VALIDATION + "\n\ndef approve_order(order):\n    return validate_order(order)\n",
        encoding="utf-8",
    )
    reconcile_workspace(workspace)

    assert _null_handles(workspace) == 0
    changed = _rendered_handles(_orient(workspace, ["approve_order"]))
    unchanged = _rendered_handles(_orient(workspace, ["build_report"]))[:1]
    assert changed and unchanged
    inspected = _inspect(workspace, [*changed[:2], *unchanged])
    assert _missing(inspected) == []
