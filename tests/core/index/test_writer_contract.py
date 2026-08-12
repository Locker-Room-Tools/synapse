"""`INDEX_WRITER_CONTRACT_VERSION` is a promise; this is what it promises.

The version is hand-maintained, so its only real failure mode is a write-path change
that breaks an invariant without a bump. Each invariant is asserted here, in the module
that names the constant, so such a change fails a test that points straight at it.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

from synapse.core.index import INDEX_WRITER_CONTRACT_VERSION, SymbolIndex, symbol_handle
from synapse.core.models import Confidence, SourceFile, Symbol, SymbolKind


def _symbol(symbol_id: str, name: str, *, file_path: str, line: int = 1) -> Symbol:
    return Symbol(
        id=symbol_id,
        language="python",
        kind=SymbolKind.FUNCTION,
        native_kind="function_definition",
        name=name,
        qualified_name=name,
        file_path=file_path,
        container_id=None,
        start_line=line,
        end_line=line + 1,
        start_byte=0,
        end_byte=10,
        signature=f"def {name}():",
        source="tree-sitter",
        confidence=Confidence.HIGH,
    )


def _index(tmp_path: Path, file_path: str = "app/one.py") -> SymbolIndex:
    index = SymbolIndex(tmp_path / "index.sqlite")
    index.upsert_file(
        SourceFile(
            id=file_path,
            path=file_path,
            language="python",
            project_root="/workspace",
            content_hash="hash",
            indexed_at="2026-01-01T00:00:00Z",
        )
    )
    return index


def test_the_declared_contract_version_is_a_plain_positive_integer() -> None:
    """Not a version string and not derived from the package version."""
    assert isinstance(INDEX_WRITER_CONTRACT_VERSION, int)
    assert not isinstance(INDEX_WRITER_CONTRACT_VERSION, bool)
    assert INDEX_WRITER_CONTRACT_VERSION >= 1


def test_contract_invariant_every_written_symbol_carries_its_derived_handle(
    tmp_path: Path,
) -> None:
    """Invariant one. Breaking this without bumping the version is the failure to catch."""
    index = _index(tmp_path)
    symbols = [_symbol("py:one", "alpha", file_path="app/one.py", line=1)]
    symbols.append(_symbol("py:two", "beta", file_path="app/one.py", line=5))

    index.replace_symbols_for_file("app/one.py", symbols)

    with closing(sqlite3.connect(tmp_path / "index.sqlite")) as connection:
        rows = connection.execute("SELECT id, handle FROM symbols").fetchall()
    assert {str(row[0]): str(row[1]) for row in rows} == {
        "py:one": symbol_handle("py:one"),
        "py:two": symbol_handle("py:two"),
    }


def test_contract_invariant_replacing_a_file_deletes_before_it_inserts(tmp_path: Path) -> None:
    """Invariant two: a file's previous rows never survive its replacement."""
    index = _index(tmp_path)
    index.replace_symbols_for_file(
        "app/one.py", [_symbol("py:one", "alpha", file_path="app/one.py")]
    )

    index.replace_symbols_for_file(
        "app/one.py", [_symbol("py:two", "beta", file_path="app/one.py", line=5)]
    )

    with closing(sqlite3.connect(tmp_path / "index.sqlite")) as connection:
        rows = connection.execute("SELECT id FROM symbols").fetchall()
    assert [str(row[0]) for row in rows] == ["py:two"]


def test_contract_invariant_persisted_handles_are_unique(tmp_path: Path) -> None:
    """Invariant three, enforced by the file rather than by the writer's good intentions."""
    with closing(sqlite3.connect(SymbolIndex(tmp_path / "index.sqlite").db_path)) as connection:
        indexes = {
            str(row[1]): bool(row[2])
            for row in connection.execute("PRAGMA index_list(symbols)").fetchall()
        }
    assert indexes["idx_symbols_handle"] is True
