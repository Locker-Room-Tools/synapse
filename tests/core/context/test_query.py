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


def test_unmatched_question_falls_back_to_structural_seeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unmatched identifier is not proof of absence: fallback fires with a reason."""
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _build_chain(index)
    result = query_context(
        index,
        ContextQuery(question="where is `NoSuchThing` used?"),
        workspace_root=workspace_root,
    )
    payload = json.loads(result)
    assert payload["seeds"]
    assert all(seed["match"] == "structural" for seed in payload["seeds"])
    assert payload["coverage"]["seeds"]["origin"] == "structural-fallback"
    assert payload["coverage"]["seeds"]["fallback_reason"] == "no-question-match"
    assert "zero_result" not in payload["coverage"]
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
    assert payload["coverage"]["seeds"]["origin"] == "structural-fallback"


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


def _build_multi_area_repo(index: SymbolIndex) -> None:
    """Production symbols in three areas with exact references, plus a test file."""
    _add_file(
        index,
        "src/api/server.py",
        [_symbol("sym-server", "Server", "src/api/server.py", kind=SymbolKind.CLASS)],
        [_reference("ref-sd", "sym-server", "sym-db", file_path="src/api/server.py")],
    )
    _add_file(
        index,
        "src/store/db.py",
        [_symbol("sym-db", "Database", "src/store/db.py", kind=SymbolKind.CLASS)],
        [],
    )
    _add_file(
        index,
        "src/cli/main.py",
        [_symbol("sym-main", "main", "src/cli/main.py")],
        [_reference("ref-ms", "sym-main", "sym-server", file_path="src/cli/main.py")],
    )
    _add_file(
        index,
        "tests/test_api.py",
        [_symbol("sym-test-api", "test_api", "tests/test_api.py")],
        [_reference("ref-ts", "sym-test-api", "sym-server", file_path="tests/test_api.py")],
    )


def test_russian_architecture_question_gets_structural_orientation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _build_multi_area_repo(index)
    result = query_context(
        index,
        ContextQuery(
            question="Объясни архитектуру всего репозитория, поток запросов и ключевые точки входа."
        ),
        workspace_root=workspace_root,
    )
    payload = json.loads(result)
    assert payload["coverage"]["seeds"]["origin"] == "structural-fallback"
    assert "zero_result" not in payload["coverage"]
    seed_files = {seed["file"] for seed in payload["seeds"]}
    assert len({file.rsplit("/", 1)[0] for file in seed_files}) >= 2
    assert all("tests/" not in file for file in seed_files)
    assert len(result) <= payload["query"]["token_budget"] * CHARS_PER_TOKEN


def test_identifier_free_english_question_uses_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _build_multi_area_repo(index)
    result = query_context(
        index,
        ContextQuery(question="How does everything fit together in here?"),
        workspace_root=workspace_root,
    )
    payload = json.loads(result)
    assert payload["coverage"]["seeds"]["origin"] == "structural-fallback"
    assert payload["seeds"]


def test_mixed_language_question_with_identifier_skips_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _build_multi_area_repo(index)
    result = query_context(
        index,
        ContextQuery(question="Объясни `Database` подробно"),
        workspace_root=workspace_root,
    )
    payload = json.loads(result)
    assert payload["coverage"]["seeds"]["origin"] == "question-match"
    assert [seed["id"] for seed in payload["seeds"]] == ["sym-db"]
    assert payload["seeds"][0]["match"] == "exact-name"


def test_only_test_matches_are_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _add_file(
        index,
        "tests/test_widget.py",
        [_symbol("sym-tw", "widget_fixture", "tests/test_widget.py")],
        [],
    )
    result = query_context(
        index,
        ContextQuery(question="explain `widget_fixture`"),
        workspace_root=workspace_root,
    )
    payload = json.loads(result)
    assert payload["coverage"]["seeds"]["origin"] == "question-match"
    assert payload["coverage"]["seeds"]["only_test_matches"] is True
    assert payload["seeds"][0]["test_path"] is True


def _build_cross_link_graph(index: SymbolIndex) -> None:
    """A -> B, A -> C tree edges plus B -> C cross-link and C -> A cycle edge."""
    _add_file(
        index,
        "src/a.py",
        [_symbol("sym-a", "alpha", "src/a.py")],
        [
            _reference("ref-ab", "sym-a", "sym-b", file_path="src/a.py"),
            _reference("ref-ac", "sym-a", "sym-c", file_path="src/a.py"),
        ],
    )
    _add_file(
        index,
        "src/b.py",
        [_symbol("sym-b", "beta", "src/b.py")],
        [_reference("ref-bc", "sym-b", "sym-c", file_path="src/b.py")],
    )
    _add_file(
        index,
        "src/c.py",
        [_symbol("sym-c", "gamma", "src/c.py")],
        [_reference("ref-ca", "sym-c", "sym-a", file_path="src/c.py")],
    )


def test_non_tree_edges_are_projected_with_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _build_cross_link_graph(index)
    result = query_context(
        index,
        ContextQuery(question="trace `alpha`", direction=Direction.OUT),
        workspace_root=workspace_root,
    )
    payload = json.loads(result)
    edges = payload["edges"]
    edge_pairs = {(edge["from"], edge["to"]) for edge in edges}
    assert ("sym-b", "sym-c") in edge_pairs
    assert ("sym-c", "sym-a") in edge_pairs
    cross = next(edge for edge in edges if edge["from"] == "sym-b")
    assert cross["edge"] == "references"
    assert cross["res"] == "exact"
    assert cross["conf"] == "high"
    assert cross["at"] == "src/b.py:2"
    edge_coverage = payload["coverage"]["projection"]["edges"]
    assert edge_coverage["discovered"] == 4
    assert edge_coverage["tree_projected"] == 2
    assert edge_coverage["extra_projected"] == 2
    assert edge_coverage["omitted"] == 0


def test_edge_projection_counts_stay_consistent_under_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    symbols = [_symbol("sym-hub", "hub", "src/hub.py")]
    relations = []
    for i in range(40):
        symbols.append(
            _symbol(f"sym-n{i:02d}", f"member{i:02d}", "src/hub.py", start_line=i * 7 + 10)
        )
        relations.append(
            _reference(f"ref-t{i:02d}", "sym-hub", f"sym-n{i:02d}", file_path="src/hub.py")
        )
    for i in range(20):
        relations.append(
            _reference(
                f"ref-x{i:02d}",
                f"sym-n{i:02d}",
                f"sym-n{(i + 1) % 20:02d}",
                file_path="src/hub.py",
            )
        )
    _add_file(index, "src/hub.py", symbols, relations)
    result = query_context(
        index,
        ContextQuery(
            question="explain `hub`", direction=Direction.OUT, token_budget=MIN_TOKEN_BUDGET
        ),
        workspace_root=workspace_root,
    )
    assert len(result) <= MIN_TOKEN_BUDGET * CHARS_PER_TOKEN
    payload = json.loads(result)
    edge_coverage = payload["coverage"]["projection"]["edges"]
    assert (
        edge_coverage["discovered"]
        == edge_coverage["tree_projected"]
        + edge_coverage["extra_projected"]
        + edge_coverage["omitted"]
    )
    assert edge_coverage["omitted"] > 0


def test_ambiguous_seed_references_surface_as_unresolved_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Null-target references from seeds are shown with resolution and site."""
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _add_file(
        index,
        "src/tool.py",
        [_symbol("sym-tool", "the_tool", "src/tool.py")],
        [
            Relation(
                id="ref-amb",
                kind=RelationKind.REFERENCES,
                from_symbol_id="sym-tool",
                to_symbol_id=None,
                from_file_path="src/tool.py",
                to_file_path=None,
                to_name="find_things",
                source="test",
                confidence=Confidence.LOW,
                start_line=5,
                start_byte_col=0,
                resolution=ResolutionMethod.AMBIGUOUS,
            )
        ],
    )
    result = query_context(
        index,
        ContextQuery(question="trace `the_tool`", direction=Direction.OUT),
        workspace_root=workspace_root,
    )
    payload = json.loads(result)
    assert payload["unresolved"] == {
        "items": [{"name": "find_things", "res": "ambiguous", "at": "src/tool.py:5"}],
        "total": 1,
    }


def _build_test_heavy_hub(index: SymbolIndex) -> None:
    """One production hub called by 2 production callers and 12 test callers."""
    _add_file(index, "src/hub.py", [_symbol("sym-hub", "publish_event", "src/hub.py")], [])
    for i in range(2):
        _add_file(
            index,
            f"src/caller_{i}.py",
            [_symbol(f"sym-prod-{i}", f"caller_{i}", f"src/caller_{i}.py")],
            [
                _reference(
                    f"ref-prod-{i}", f"sym-prod-{i}", "sym-hub", file_path=f"src/caller_{i}.py"
                )
            ],
        )
    for i in range(12):
        _add_file(
            index,
            f"tests/test_caller_{i:02d}.py",
            [_symbol(f"sym-t{i:02d}", f"test_caller_{i:02d}", f"tests/test_caller_{i:02d}.py")],
            [
                _reference(
                    f"ref-t{i:02d}",
                    f"sym-t{i:02d}",
                    "sym-hub",
                    file_path=f"tests/test_caller_{i:02d}.py",
                )
            ],
        )


def test_production_focus_caps_and_accounts_for_test_nodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _build_test_heavy_hub(index)
    result = query_context(
        index,
        ContextQuery(question="who publishes events? `publish_event`"),
        workspace_root=workspace_root,
    )
    payload = json.loads(result)
    projection = payload["coverage"]["projection"]
    assert projection["policy"] == "production-focus"
    assert projection["tests"] == {"discovered": 12, "projected": 5, "demoted": 7}
    node_files = [node["file"] for node in payload["nodes"]]
    production_nodes = [file for file in node_files if not file.startswith("tests/")]
    test_nodes = [file for file in node_files if file.startswith("tests/")]
    assert len(production_nodes) == 2
    assert len(test_nodes) == 5


def test_explicit_test_symbol_query_keeps_full_test_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _build_test_heavy_hub(index)
    result = query_context(
        index,
        ContextQuery(question="explain `test_caller_00`"),
        workspace_root=workspace_root,
    )
    payload = json.loads(result)
    assert payload["coverage"]["projection"]["policy"] == "test-relevant"
    assert payload["seeds"][0]["test_path"] is True


def test_impact_query_still_surfaces_relevant_tests_within_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _build_test_heavy_hub(index)
    result = query_context(
        index,
        ContextQuery(question="what would break if `publish_event` changes?"),
        workspace_root=workspace_root,
    )
    payload = json.loads(result)
    test_nodes = [node for node in payload["nodes"] if node["file"].startswith("tests/")]
    assert 1 <= len(test_nodes) <= 5
    assert payload["coverage"]["projection"]["tests"]["discovered"] == 12
