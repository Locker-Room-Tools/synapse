"""Tests for bounded deterministic BFS traversal."""

from pathlib import Path

from synapse.core.context import Direction, TraversalLimits, traverse
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


def _symbol(
    symbol_id: str,
    name: str,
    file_path: str,
    *,
    kind: SymbolKind = SymbolKind.FUNCTION,
    start_line: int = 1,
) -> Symbol:
    return Symbol(
        id=symbol_id,
        language="python",
        kind=kind,
        native_kind="test",
        name=name,
        qualified_name=name,
        file_path=file_path,
        container_id=None,
        start_line=start_line,
        end_line=start_line + 1,
        start_byte=start_line * 100,
        end_byte=start_line * 100 + 10,
        signature=None,
        source="test",
        confidence=Confidence.HIGH,
    )


def _reference(
    relation_id: str,
    from_symbol_id: str | None,
    to_symbol_id: str | None,
    *,
    file_path: str,
    resolution: ResolutionMethod = ResolutionMethod.EXACT,
    confidence: Confidence = Confidence.HIGH,
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
        confidence=confidence,
        start_line=2,
        start_byte_col=0,
        resolution=resolution,
    )


def _contains(relation_id: str, container_id: str, child_id: str, *, file_path: str) -> Relation:
    return Relation(
        id=relation_id,
        kind=RelationKind.CONTAINS,
        from_symbol_id=container_id,
        to_symbol_id=child_id,
        from_file_path=file_path,
        to_file_path=file_path,
        to_name=None,
        source="test",
        confidence=Confidence.HIGH,
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


def _build_graph(tmp_path: Path) -> SymbolIndex:
    """A -> B -> C -> A cycle, D -> A incoming, container P contains A, imports in a.py."""
    index = SymbolIndex(tmp_path / "index.sqlite")
    files = {
        "a.py": (
            [_symbol("sym-a", "alpha", "a.py"), _symbol("sym-p", "Parent", "a.py", start_line=20)],
            [
                _reference("ref-ab", "sym-a", "sym-b", file_path="a.py"),
                _contains("con-pa", "sym-p", "sym-a", file_path="a.py"),
                _import("imp-a", "a.py", "os"),
            ],
        ),
        "b.py": (
            [_symbol("sym-b", "beta", "b.py")],
            [
                _reference(
                    "ref-bc",
                    "sym-b",
                    "sym-c",
                    file_path="b.py",
                    resolution=ResolutionMethod.SCOPED,
                    confidence=Confidence.MEDIUM,
                )
            ],
        ),
        "c.py": (
            [_symbol("sym-c", "gamma", "c.py")],
            [_reference("ref-ca", "sym-c", "sym-a", file_path="c.py")],
        ),
        "d.py": (
            [_symbol("sym-d", "delta", "d.py")],
            [
                _reference(
                    "ref-da",
                    "sym-d",
                    "sym-a",
                    file_path="d.py",
                    resolution=ResolutionMethod.UNIQUE_NAME,
                    confidence=Confidence.MEDIUM,
                )
            ],
        ),
    }
    for file_path, (symbols, relations) in files.items():
        index.upsert_file(
            SourceFile(
                id=file_path,
                path=file_path,
                language="python",
                project_root=None,
                content_hash=f"hash-{file_path}",
                indexed_at="2026-07-30T00:00:00+00:00",
            )
        )
        index.replace_symbols_for_file(file_path, symbols, relations)
    return index


def _seed(index: SymbolIndex, symbol_id: str) -> Symbol:
    with index.read_session() as reads:
        return reads.get_symbols_by_ids([symbol_id])[symbol_id]


def test_outgoing_traversal_follows_cycle_without_looping(tmp_path: Path) -> None:
    index = _build_graph(tmp_path)
    seed = _seed(index, "sym-a")
    with index.read_session() as reads:
        outcome = traverse(reads, [seed], Direction.OUT, TraversalLimits(max_depth=5))
    assert sorted(outcome.nodes) == ["sym-a", "sym-b", "sym-c"]
    assert [edge.relation.id for edge in outcome.edges] == ["ref-ab", "ref-bc", "ref-ca"]
    assert outcome.depth_reached == 3
    assert not any(outcome.guards.values())
    assert outcome.nodes["sym-b"].parent_edge_id == "ref-ab"
    assert outcome.nodes["sym-c"].parent_edge_id == "ref-bc"


def test_incoming_traversal_discovers_referencers_and_container(tmp_path: Path) -> None:
    index = _build_graph(tmp_path)
    seed = _seed(index, "sym-a")
    with index.read_session() as reads:
        outcome = traverse(reads, [seed], Direction.IN, TraversalLimits(max_depth=1))
    assert sorted(outcome.nodes) == ["sym-a", "sym-c", "sym-d", "sym-p"]
    directions = {edge.relation.id: edge.direction for edge in outcome.edges}
    assert directions == {"con-pa": "in", "ref-ca": "in", "ref-da": "in"}


def test_bidirectional_traversal_deduplicates_edges(tmp_path: Path) -> None:
    index = _build_graph(tmp_path)
    seed = _seed(index, "sym-a")
    with index.read_session() as reads:
        outcome = traverse(reads, [seed], Direction.BOTH, TraversalLimits(max_depth=3))
    edge_ids = [edge.relation.id for edge in outcome.edges]
    assert len(edge_ids) == len(set(edge_ids))
    assert sorted(outcome.nodes) == ["sym-a", "sym-b", "sym-c", "sym-d", "sym-p"]


def test_imports_are_never_traversed(tmp_path: Path) -> None:
    index = _build_graph(tmp_path)
    seed = _seed(index, "sym-a")
    with index.read_session() as reads:
        outcome = traverse(reads, [seed], Direction.BOTH, TraversalLimits(max_depth=5))
    assert all(edge.relation.kind is not RelationKind.IMPORTS for edge in outcome.edges)


def test_resolution_and_confidence_are_preserved_verbatim(tmp_path: Path) -> None:
    index = _build_graph(tmp_path)
    seed = _seed(index, "sym-a")
    with index.read_session() as reads:
        outcome = traverse(reads, [seed], Direction.OUT, TraversalLimits(max_depth=2))
    scoped = next(edge for edge in outcome.edges if edge.relation.id == "ref-bc")
    assert scoped.relation.resolution is ResolutionMethod.SCOPED
    assert scoped.relation.confidence is Confidence.MEDIUM


def test_max_nodes_guard_stops_expansion(tmp_path: Path) -> None:
    index = _build_graph(tmp_path)
    seed = _seed(index, "sym-a")
    with index.read_session() as reads:
        outcome = traverse(reads, [seed], Direction.BOTH, TraversalLimits(max_depth=3, max_nodes=2))
    assert outcome.guards["max_nodes"] is True
    assert len(outcome.nodes) <= 2


def test_max_total_edges_guard_stops_expansion(tmp_path: Path) -> None:
    index = _build_graph(tmp_path)
    seed = _seed(index, "sym-a")
    with index.read_session() as reads:
        outcome = traverse(
            reads, [seed], Direction.BOTH, TraversalLimits(max_depth=3, max_total_edges=1)
        )
    assert outcome.guards["max_total_edges"] is True
    assert len(outcome.edges) == 1


def test_depth_guard_reports_remaining_frontier(tmp_path: Path) -> None:
    index = _build_graph(tmp_path)
    seed = _seed(index, "sym-a")
    with index.read_session() as reads:
        outcome = traverse(reads, [seed], Direction.OUT, TraversalLimits(max_depth=1))
    assert outcome.guards["max_depth"] is True
    assert outcome.frontier_remaining == 1


def test_hub_fanout_is_suppressed_with_counts(tmp_path: Path) -> None:
    index = SymbolIndex(tmp_path / "index.sqlite")
    symbols = [_symbol("sym-hub", "hub", "hub.py")] + [
        _symbol(f"sym-u{i}", f"user{i}", "hub.py", start_line=i * 5 + 10) for i in range(5)
    ]
    relations = [
        _reference(f"ref-u{i}", f"sym-u{i}", "sym-hub", file_path="hub.py") for i in range(5)
    ]
    index.upsert_file(
        SourceFile(
            id="hub.py",
            path="hub.py",
            language="python",
            project_root=None,
            content_hash="hash-hub",
            indexed_at="2026-07-30T00:00:00+00:00",
        )
    )
    index.replace_symbols_for_file("hub.py", symbols, relations)
    seed = _seed(index, "sym-hub")
    with index.read_session() as reads:
        outcome = traverse(
            reads, [seed], Direction.IN, TraversalLimits(max_depth=1, max_edges_per_node=2)
        )
    assert len(outcome.edges) == 2
    assert outcome.suppressed == {"sym-hub": 3}


def test_unresolved_endpoints_are_counted_not_traversed(tmp_path: Path) -> None:
    index = SymbolIndex(tmp_path / "index.sqlite")
    index.upsert_file(
        SourceFile(
            id="a.py",
            path="a.py",
            language="python",
            project_root=None,
            content_hash="hash-a",
            indexed_at="2026-07-30T00:00:00+00:00",
        )
    )
    index.replace_symbols_for_file(
        "a.py",
        [_symbol("sym-a", "alpha", "a.py")],
        [
            _reference(
                "ref-null",
                "sym-a",
                None,
                file_path="a.py",
                resolution=ResolutionMethod.UNRESOLVED,
                confidence=Confidence.LOW,
            )
        ],
    )
    seed = _seed(index, "sym-a")
    with index.read_session() as reads:
        outcome = traverse(reads, [seed], Direction.OUT, TraversalLimits(max_depth=2))
    assert outcome.unresolved_edges == 1
    assert list(outcome.nodes) == ["sym-a"]
    assert outcome.edges == ()


def test_traversal_is_deterministic(tmp_path: Path) -> None:
    index = _build_graph(tmp_path)
    seed = _seed(index, "sym-a")
    with index.read_session() as reads:
        first = traverse(reads, [seed], Direction.BOTH, TraversalLimits())
        second = traverse(reads, [seed], Direction.BOTH, TraversalLimits())
    assert first == second


def test_empty_seed_list_yields_empty_outcome(tmp_path: Path) -> None:
    index = _build_graph(tmp_path)
    with index.read_session() as reads:
        outcome = traverse(reads, [], Direction.BOTH, TraversalLimits())
    assert outcome.nodes == {}
    assert outcome.edges == ()
    assert not any(outcome.guards.values())
