"""Tests for batched read helpers and the consistent read session."""

from pathlib import Path

from synapse.core.index import SymbolIndex
from synapse.core.models import (
    Confidence,
    Relation,
    RelationKind,
    ResolutionMethod,
    SourceFile,
    Symbol,
    SymbolKind,
)


def _source_file(file_id: str) -> SourceFile:
    return SourceFile(
        id=file_id,
        path=file_id,
        language="python",
        project_root=None,
        content_hash=f"hash-{file_id}",
        indexed_at="2026-07-30T00:00:00+00:00",
    )


def _symbol(symbol_id: str, name: str, file_path: str, *, start_byte: int = 0) -> Symbol:
    return Symbol(
        id=symbol_id,
        language="python",
        kind=SymbolKind.FUNCTION,
        native_kind="test",
        name=name,
        qualified_name=name,
        file_path=file_path,
        container_id=None,
        start_line=start_byte + 1,
        end_line=start_byte + 2,
        start_byte=start_byte,
        end_byte=start_byte + 10,
        signature=None,
        source="test",
        confidence=Confidence.HIGH,
    )


def _reference(
    relation_id: str,
    from_symbol_id: str | None,
    to_symbol_id: str | None,
    *,
    file_path: str = "a.py",
    resolution: ResolutionMethod = ResolutionMethod.EXACT,
) -> Relation:
    return Relation(
        id=relation_id,
        kind=RelationKind.REFERENCES,
        from_symbol_id=from_symbol_id,
        to_symbol_id=to_symbol_id,
        from_file_path=file_path,
        to_file_path=None,
        to_name="target",
        source="test",
        confidence=Confidence.HIGH,
        start_line=3,
        start_byte_col=4,
        resolution=resolution,
    )


def _import(relation_id: str, file_path: str, name: str) -> Relation:
    return Relation(
        id=relation_id,
        kind=RelationKind.IMPORTS,
        from_symbol_id=None,
        to_symbol_id=None,
        from_file_path=file_path,
        to_file_path=None,
        to_name=name,
        source="test",
        confidence=Confidence.HIGH,
    )


def _build_two_file_index(tmp_path: Path) -> SymbolIndex:
    index = SymbolIndex(tmp_path / "index.sqlite")
    index.upsert_file(_source_file("a.py"))
    index.upsert_file(_source_file("b.py"))
    index.replace_symbols_for_file(
        "a.py",
        [_symbol("sym-a1", "alpha", "a.py"), _symbol("sym-a2", "beta", "a.py", start_byte=20)],
        [
            _reference("ref-1", "sym-a1", "sym-b1"),
            _reference("ref-2", "sym-a2", "sym-b1", resolution=ResolutionMethod.SCOPED),
            _reference("ref-3", "sym-a1", None, resolution=ResolutionMethod.UNRESOLVED),
            _import("imp-1", "a.py", "os"),
            _import("imp-2", "a.py", "pathlib.Path"),
        ],
    )
    index.replace_symbols_for_file(
        "b.py",
        [_symbol("sym-b1", "gamma", "b.py")],
        [_import("imp-3", "b.py", "json")],
    )
    return index


def test_get_symbols_by_ids_returns_found_and_omits_missing(tmp_path: Path) -> None:
    index = _build_two_file_index(tmp_path)
    with index.read_session() as reads:
        symbols = reads.get_symbols_by_ids(["sym-b1", "sym-a1", "missing", "sym-a1"])
    assert sorted(symbols) == ["sym-a1", "sym-b1"]
    assert symbols["sym-a1"].name == "alpha"


def test_relation_batches_are_deterministic_and_kind_filtered(tmp_path: Path) -> None:
    index = _build_two_file_index(tmp_path)
    with index.read_session() as reads:
        outgoing = reads.relations_from_symbols(
            ["sym-a2", "sym-a1"], kinds=(RelationKind.REFERENCES,)
        )
        incoming = reads.relations_to_symbols(["sym-b1"], kinds=(RelationKind.REFERENCES,))
        no_kinds = reads.relations_from_symbols(["sym-a1"], kinds=())
    assert [relation.id for relation in outgoing] == ["ref-1", "ref-3", "ref-2"]
    assert [relation.id for relation in incoming] == ["ref-1", "ref-2"]
    assert incoming[1].resolution is ResolutionMethod.SCOPED
    assert no_kinds == []


def test_relations_batch_over_the_in_clause_limit(tmp_path: Path) -> None:
    index = SymbolIndex(tmp_path / "index.sqlite")
    index.upsert_file(_source_file("a.py"))
    symbols = [_symbol(f"sym-{i:04d}", f"name{i}", "a.py", start_byte=i * 3) for i in range(501)]
    relations = [_reference(f"ref-{i:04d}", f"sym-{i:04d}", "sym-0000") for i in range(501)]
    index.replace_symbols_for_file("a.py", symbols, relations)
    with index.read_session() as reads:
        found = reads.get_symbols_by_ids([symbol.id for symbol in symbols])
        outgoing = reads.relations_from_symbols(
            [symbol.id for symbol in symbols], kinds=(RelationKind.REFERENCES,)
        )
    assert len(found) == 501
    assert [relation.id for relation in outgoing] == [f"ref-{i:04d}" for i in range(501)]


def test_imports_for_files_groups_names_per_file(tmp_path: Path) -> None:
    index = _build_two_file_index(tmp_path)
    with index.read_session() as reads:
        imports = reads.imports_for_files(["b.py", "a.py", "unknown.py"])
    assert imports == {"a.py": ["os", "pathlib.Path"], "b.py": ["json"]}


def test_read_session_pins_one_snapshot_across_concurrent_writes(tmp_path: Path) -> None:
    index = _build_two_file_index(tmp_path)
    writer = SymbolIndex(tmp_path / "index.sqlite")
    with index.read_session() as reads:
        before = reads.get_symbols_by_ids(["sym-c1"])
        writer.upsert_file(_source_file("c.py"))
        writer.replace_symbols_for_file("c.py", [_symbol("sym-c1", "delta", "c.py")], [])
        during = reads.get_symbols_by_ids(["sym-c1"])
    with index.read_session() as reads:
        after = reads.get_symbols_by_ids(["sym-c1"])
    assert before == {}
    assert during == {}
    assert list(after) == ["sym-c1"]
