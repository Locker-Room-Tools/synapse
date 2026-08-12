"""Read-only integrity probes and deterministic repair for persisted handles.

Orientation renders a public handle from a symbol's stable id, while inspection
resolves caller-supplied handles through the persisted `symbols.handle` column. A row
whose handle is missing or malformed therefore renders a handle that can never be
resolved, so handle completeness is a navigation readiness invariant.

The probe opens the database through a `mode=ro` URI and never constructs `SymbolIndex`:
it runs while a watch daemon — possibly one from an older build — owns the file, and
constructing `SymbolIndex` would migrate it.
"""

import sqlite3
from pathlib import Path
from urllib.parse import quote

from synapse.core.index.contract import SCHEMA_VERSION
from synapse.core.index.handles import symbol_handle


class IndexIntegrityError(RuntimeError):
    """Raised when persisted index state violates a handle invariant."""


def handle_completeness_reason(database_path: Path) -> str | None:
    """Return why persisted handles cannot be trusted, or None when they can.

    Bounded by construction: one pragma plus one `LIMIT 1` existence probe that seeks
    the null prefix of the unique handle index. It never scans the symbol table.
    """
    if not database_path.exists():
        # A missing index is reported by the caller's own earlier, cheaper check.
        return None
    try:
        connection = sqlite3.connect(f"file:{quote(str(database_path))}?mode=ro", uri=True)
    except sqlite3.Error:
        return "unreadable-index"
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version < SCHEMA_VERSION:
            # Such a file carries no handle constraint, so completeness cannot be
            # guaranteed for future writes even when it happens to hold right now.
            return "handles-unenforced"
        row = connection.execute("SELECT 1 FROM symbols WHERE handle IS NULL LIMIT 1").fetchone()
    except sqlite3.Error:
        return "unreadable-index"
    finally:
        connection.close()
    return "incomplete-handles" if row is not None else None


def repair_symbol_handles(connection: sqlite3.Connection) -> int:
    """Rewrite every persisted handle that is not exactly `symbol_handle(id)`.

    Recomputes and compares each row rather than testing the stored text for shape.
    Shape is not sufficient: a handle can carry the right length and prefix and still be
    unusable (characters outside the base64url alphabet), or be perfectly well-formed
    but belong to a different stable id. Both render orientation's handle unresolvable,
    and neither is visible to a format test.

    Deterministic, because `symbol_handle` is a pure function of the stable id, and
    idempotent, because a second pass finds no differences and returns 0. The scan is
    acceptable here: this runs only under migration or repair, never on a navigation
    call, where completeness is probed by a bounded index seek instead.
    """
    rows = connection.execute("SELECT id, handle FROM symbols").fetchall()
    updates = [
        (expected, symbol_id)
        for symbol_id, stored in ((str(row[0]), row[1]) for row in rows)
        if (expected := symbol_handle(symbol_id)) != stored
    ]
    if not updates:
        return 0
    connection.executemany("UPDATE symbols SET handle = ? WHERE id = ?", updates)
    return len(updates)
