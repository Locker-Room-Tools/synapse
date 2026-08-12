"""Trust policy ranks and batched one-hop retrieval."""

from pathlib import Path

from synapse.core.models import Confidence, ResolutionMethod
from synapse.core.navigation.traversal import (
    confidence_rank,
    edge_sort_key,
    edge_trust,
    one_hop,
    resolution_rank,
)
from tests.core.navigation.builders import (
    add_file,
    build_index,
    make_contains,
    make_reference,
    make_symbol,
)


def test_edge_trust_classifies_stored_resolution_verbatim() -> None:
    exact = make_reference("r1", from_symbol_id="a", to_symbol_id="b", from_file_path="a.py")
    scoped = make_reference(
        "r2",
        from_symbol_id="a",
        to_symbol_id="b",
        from_file_path="a.py",
        resolution=ResolutionMethod.SCOPED,
    )
    unique = make_reference(
        "r3",
        from_symbol_id="a",
        to_symbol_id="b",
        from_file_path="a.py",
        resolution=ResolutionMethod.UNIQUE_NAME,
    )
    unclassified = make_reference(
        "r4", from_symbol_id="a", to_symbol_id="b", from_file_path="a.py", resolution=None
    )
    containment = make_contains("a", "b", "a.py")

    assert edge_trust(exact) == "exact"
    assert edge_trust(scoped) == "scoped"
    assert edge_trust(unique) == "heuristic"
    assert edge_trust(unclassified) == "heuristic"
    assert edge_trust(containment) == "exact"


def test_resolution_and_confidence_ranks_order_best_first() -> None:
    ordered = [
        ResolutionMethod.EXACT,
        ResolutionMethod.SCOPED,
        ResolutionMethod.UNIQUE_NAME,
        None,
        ResolutionMethod.AMBIGUOUS,
        ResolutionMethod.UNRESOLVED,
    ]
    ranks = [resolution_rank(resolution) for resolution in ordered]
    assert ranks == sorted(ranks)
    assert confidence_rank(Confidence.HIGH) < confidence_rank(Confidence.MEDIUM)
    assert confidence_rank(Confidence.MEDIUM) < confidence_rank(Confidence.LOW)


def test_one_hop_groups_and_sorts_relations(tmp_path: Path) -> None:
    index = build_index(tmp_path)
    target = make_symbol("py:target", "target", "app/target.py")
    caller_one = make_symbol("py:caller-one", "caller_one", "app/one.py")
    caller_two = make_symbol("py:caller-two", "caller_two", "app/two.py")
    callee = make_symbol("py:callee", "callee", "app/callee.py")
    add_file(index, "app/target.py", [target])
    add_file(
        index,
        "app/one.py",
        [caller_one],
        [
            make_reference(
                "r-weak",
                from_symbol_id="py:caller-one",
                to_symbol_id="py:target",
                from_file_path="app/one.py",
                resolution=ResolutionMethod.UNIQUE_NAME,
                line=3,
            ),
            make_reference(
                "r-exact",
                from_symbol_id="py:caller-one",
                to_symbol_id="py:target",
                from_file_path="app/one.py",
                resolution=ResolutionMethod.EXACT,
                line=9,
            ),
        ],
    )
    add_file(
        index,
        "app/two.py",
        [caller_two],
        [
            make_reference(
                "r-scoped",
                from_symbol_id="py:caller-two",
                to_symbol_id="py:target",
                from_file_path="app/two.py",
                resolution=ResolutionMethod.SCOPED,
                line=5,
            )
        ],
    )
    add_file(index, "app/callee.py", [callee])
    add_file(
        index,
        "app/target-out.py",
        [make_symbol("py:out-holder", "out_holder", "app/target-out.py")],
        [
            make_reference(
                "r-out",
                from_symbol_id="py:target",
                to_symbol_id="py:callee",
                from_file_path="app/target.py",
            )
        ],
    )

    with index.read_session() as reads:
        hop = one_hop(reads, ["py:target"])

    incoming = hop.incoming["py:target"]
    assert [relation.id for relation in incoming] == ["r-exact", "r-scoped", "r-weak"]
    assert incoming == sorted(incoming, key=edge_sort_key)
    outgoing = hop.outgoing["py:target"]
    assert [relation.id for relation in outgoing] == ["r-out"]
    assert "py:caller-one" not in hop.incoming
