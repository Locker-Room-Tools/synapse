"""Integration tests for the budgeted context query orchestration."""

import json
from pathlib import Path

import pytest

from synapse.core.context import ContextQuery, Direction, query_context
from synapse.core.context.budget import CHARS_PER_TOKEN, MIN_TOKEN_BUDGET
from synapse.core.index import SymbolIndex
from synapse.core.indexing import REFERENCE_FINGERPRINT_KEY, reference_extraction_fingerprint
from synapse.core.models import (
    Confidence,
    Relation,
    RelationKind,
    ResolutionMethod,
    SourceFile,
    Symbol,
    SymbolKind,
)
from synapse.core.workspace import db_path


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
        signature=f"def {name}():",
        source="test",
        confidence=Confidence.HIGH,
    )


def _reference(
    relation_id: str,
    from_symbol_id: str,
    to_symbol_id: str,
    *,
    file_path: str,
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
        start_line=2,
        start_byte_col=0,
        resolution=resolution,
    )


def _workspace_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, fresh_fingerprint: bool = True
) -> tuple[Path, SymbolIndex]:
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    index = SymbolIndex(db_path(workspace_root))
    if fresh_fingerprint:
        index.set_meta(REFERENCE_FINGERPRINT_KEY, reference_extraction_fingerprint())
    return workspace_root, index


def _add_file(
    index: SymbolIndex, file_path: str, symbols: list[Symbol], relations: list[Relation]
) -> None:
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


def _build_chain(index: SymbolIndex) -> None:
    """alpha -> beta -> gamma call chain across three files."""
    _add_file(
        index,
        "src/a.py",
        [_symbol("sym-a", "alpha", "src/a.py")],
        [_reference("ref-ab", "sym-a", "sym-b", file_path="src/a.py")],
    )
    _add_file(
        index,
        "src/b.py",
        [_symbol("sym-b", "beta", "src/b.py")],
        [
            _reference(
                "ref-bc",
                "sym-b",
                "sym-c",
                file_path="src/b.py",
                resolution=ResolutionMethod.SCOPED,
            )
        ],
    )
    _add_file(index, "src/c.py", [_symbol("sym-c", "gamma", "src/c.py")], [])


def test_query_context_returns_ordered_flow_with_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _build_chain(index)
    result = query_context(
        index,
        ContextQuery(question="How does `alpha` reach gamma?", direction=Direction.OUT),
        workspace_root=workspace_root,
    )
    payload = json.loads(result)
    assert payload["query"]["direction"] == "out"
    assert [seed["id"] for seed in payload["seeds"]] == ["sym-a"]
    assert payload["seeds"][0]["match"] == "exact-name"
    node_ids = [node["id"] for node in payload["nodes"]]
    assert node_ids == ["sym-b", "sym-c"]
    beta = payload["nodes"][0]
    assert beta["via"]["edge"] == "references"
    assert beta["via"]["res"] == "exact"
    assert beta["via"]["conf"] == "high"
    assert beta["via"]["at"] == "src/a.py:2"
    assert payload["flows"] == [
        {"ids": ["sym-a", "sym-b"], "trust": "exact"},
        {"ids": ["sym-a", "sym-b", "sym-c"], "trust": "scoped"},
    ]
    coverage = payload["coverage"]
    assert coverage["index"]["stale"] is False
    assert coverage["traversal"]["depth_reached"] == 2
    assert coverage["resolution"] == {"exact": 1, "scoped": 1}
    assert coverage["projection"]["flows_are_projections_over_stored_edges"] is True
    assert payload["truncation"]["complete"] is True


def test_query_context_is_byte_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _build_chain(index)
    query = ContextQuery(question="trace `alpha`")
    first = query_context(index, query, workspace_root=workspace_root)
    second = query_context(index, query, workspace_root=workspace_root)
    assert first == second


def test_budget_truncation_keeps_seeds_and_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    symbols = [_symbol("sym-hub", "hub", "src/hub.py")]
    relations = []
    for i in range(60):
        symbols.append(
            _symbol(f"sym-user-{i:02d}", f"consumer{i:02d}", "src/hub.py", start_line=i * 7 + 10)
        )
        relations.append(
            _reference(f"ref-{i:02d}", f"sym-user-{i:02d}", "sym-hub", file_path="src/hub.py")
        )
    _add_file(index, "src/hub.py", symbols, relations)
    result = query_context(
        index,
        ContextQuery(question="who uses `hub`?", token_budget=MIN_TOKEN_BUDGET),
        workspace_root=workspace_root,
    )
    assert len(result) <= MIN_TOKEN_BUDGET * CHARS_PER_TOKEN
    payload = json.loads(result)
    assert [seed["id"] for seed in payload["seeds"]] == ["sym-hub"]
    assert payload["truncation"]["complete"] is False
    assert payload["truncation"]["dropped"]["nodes"] > 0
    assert "coverage" in payload


def test_empty_index_is_reported_honestly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    result = query_context(
        index, ContextQuery(question="anything at all"), workspace_root=workspace_root
    )
    payload = json.loads(result)
    assert payload["seeds"] == []
    assert payload["coverage"]["zero_result"] == "empty-index"


def test_no_seed_match_is_not_proof_of_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _build_chain(index)
    result = query_context(
        index,
        ContextQuery(question="where is `NoSuchThing` used?"),
        workspace_root=workspace_root,
    )
    payload = json.loads(result)
    assert payload["seeds"] == []
    assert payload["coverage"]["zero_result"] == "no-seed-match"
    assert payload["coverage"]["index"]["symbols"] == 3


def test_unknown_explicit_ids_are_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _build_chain(index)
    result = query_context(
        index,
        ContextQuery(question="", symbol_ids=("sym-gone", "sym-also-gone")),
        workspace_root=workspace_root,
    )
    payload = json.loads(result)
    assert payload["coverage"]["unknown_symbol_ids"] == ["sym-gone", "sym-also-gone"]
    assert payload["coverage"]["zero_result"] == "unknown-symbol-ids"


def test_empty_question_and_ids_raise_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="question"):
        query_context(index, ContextQuery(question="   "), workspace_root=workspace_root)


def test_include_source_reads_seed_snippets_from_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _build_chain(index)
    source_file = workspace_root / "src" / "a.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("def alpha():\n    return beta()\n", encoding="utf-8")
    result = query_context(
        index,
        ContextQuery(question="trace `alpha`", include_source=True),
        workspace_root=workspace_root,
    )
    payload = json.loads(result)
    assert payload["source"] == [
        {"id": "sym-a", "lines": [1, 2], "text": "def alpha():\n    return beta()"}
    ]
    assert "drift" in payload["coverage"]["projection"]["source_note"]


def test_stale_reference_fingerprint_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch, fresh_fingerprint=False)
    _build_chain(index)
    result = query_context(
        index, ContextQuery(question="trace `alpha`"), workspace_root=workspace_root
    )
    payload = json.loads(result)
    assert payload["coverage"]["index"]["stale"] is True


def test_flows_prefer_production_chains_over_test_chains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _add_file(
        index,
        "src/hub.py",
        [_symbol("sym-hub", "hub", "src/hub.py")],
        [],
    )
    _add_file(
        index,
        "tests/test_hub.py",
        [_symbol("sym-test", "test_hub", "tests/test_hub.py")],
        [_reference("ref-test", "sym-test", "sym-hub", file_path="tests/test_hub.py")],
    )
    _add_file(
        index,
        "src/caller.py",
        [_symbol("sym-caller", "caller", "src/caller.py")],
        [_reference("ref-prod", "sym-caller", "sym-hub", file_path="src/caller.py")],
    )
    result = query_context(
        index, ContextQuery(question="who uses `hub`?"), workspace_root=workspace_root
    )
    payload = json.loads(result)
    assert payload["flows"][0] == {"ids": ["sym-hub", "sym-caller"], "trust": "exact"}


def test_exact_flow_outranks_longer_heuristic_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aggregate path trust ranks flows; depth never outranks confidence."""
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _add_file(
        index,
        "src/root.py",
        [_symbol("sym-root", "root", "src/root.py")],
        [
            _reference("ref-e1", "sym-root", "sym-mid", file_path="src/root.py"),
            _reference(
                "ref-w1",
                "sym-root",
                "sym-weak",
                file_path="src/root.py",
                resolution=ResolutionMethod.UNIQUE_NAME,
            ),
        ],
    )
    _add_file(
        index,
        "src/mid.py",
        [_symbol("sym-mid", "middle", "src/mid.py")],
        [
            _reference(
                "ref-w2",
                "sym-mid",
                "sym-deep",
                file_path="src/mid.py",
                resolution=ResolutionMethod.UNIQUE_NAME,
            )
        ],
    )
    _add_file(index, "src/weak.py", [_symbol("sym-weak", "weak_leaf", "src/weak.py")], [])
    _add_file(index, "src/deep.py", [_symbol("sym-deep", "deep_leaf", "src/deep.py")], [])

    result = query_context(
        index,
        ContextQuery(question="trace `root`", direction=Direction.OUT, max_depth=4),
        workspace_root=workspace_root,
    )
    payload = json.loads(result)
    flows = payload["flows"]
    # The two-hop chain ending in a heuristic edge is visibly heuristic and ranks
    # below the shorter all-exact chain.
    assert flows[0]["trust"] == "exact"
    assert flows[0]["ids"] == ["sym-root", "sym-mid"]
    heuristic_flows = [flow for flow in flows[1:] if flow["trust"] == "heuristic"]
    assert heuristic_flows
    assert all(len(flow["ids"]) >= 2 for flow in heuristic_flows)
    node_resolutions = {node["id"]: node["via"].get("res") for node in payload["nodes"]}
    assert node_resolutions["sym-weak"] == "unique-name"
