"""Ranked orientation behavior over hand-built indexes."""

import json
from pathlib import Path

import pytest

from synapse.core.index import SymbolIndex, symbol_handle
from synapse.core.models import ResolutionMethod, SymbolKind
from synapse.core.navigation import OrientRequest, orient_workspace
from synapse.core.navigation.budget import CHARS_PER_TOKEN
from tests.core.navigation.builders import (
    add_file,
    build_index,
    make_reference,
    make_symbol,
)


def _orient(
    index: SymbolIndex,
    tmp_path: Path,
    terms: tuple[str, ...] = (),
    **kwargs: object,
) -> dict[str, object]:
    request = OrientRequest(terms=terms, **kwargs)  # type: ignore[arg-type]
    result = orient_workspace(index, request, workspace_root=tmp_path)
    payload = json.loads(result)
    assert isinstance(payload, dict)
    return payload


def _match_names(payload: dict[str, object]) -> list[str]:
    matches = payload["matches"]
    assert isinstance(matches, list)
    return [str(entry["n"]) for entry in matches]


def _twin_index(tmp_path: Path) -> SymbolIndex:
    index = build_index(tmp_path)
    add_file(
        index,
        "app/service.py",
        [make_symbol("py:prod", "build_service", "app/service.py", line=10)],
    )
    add_file(
        index,
        "tests/test_service.py",
        [make_symbol("py:twin", "build_service", "tests/test_service.py", line=5)],
    )
    add_file(
        index,
        "app/decoy.py",
        [make_symbol("py:decoy", "build_service_report", "app/decoy.py", line=3)],
    )
    return index


def test_exact_production_match_ranks_before_test_twin_and_decoy(tmp_path: Path) -> None:
    index = _twin_index(tmp_path)
    payload = _orient(index, tmp_path, terms=("build_service",))

    names = _match_names(payload)
    matches = payload["matches"]
    assert isinstance(matches, list)
    assert names[0] == "build_service"
    files = payload["files"]
    assert isinstance(files, list)
    assert files[matches[0]["f"]] == "app/service.py"
    assert matches[0]["m"] == "exact"
    assert matches[0]["h"] == symbol_handle("py:prod")
    twin_positions = [
        i for i, entry in enumerate(matches) if str(files[entry["f"]]).startswith("tests/")
    ]
    assert twin_positions and min(twin_positions) > 0
    assert payload["payload_complete"] is True


def test_multi_term_evidence_outranks_a_single_exact_match(tmp_path: Path) -> None:
    """Corroboration across supplied terms beats the tier of any one match."""
    index = build_index(tmp_path)
    add_file(
        index,
        "app/store.py",
        [make_symbol("py:multi", "symbol_index_writer", "app/store.py", line=1)],
    )
    add_file(
        index,
        "app/other.py",
        [make_symbol("py:single", "index", "app/other.py", line=1)],
    )
    payload = _orient(index, tmp_path, terms=("symbol", "index", "writer"))

    names = _match_names(payload)
    # `index` matches one term exactly; `symbol_index_writer` matches all three
    # as substrings and must rank first.
    assert names.index("symbol_index_writer") < names.index("index")


def test_test_path_demotion_still_dominates_term_count(tmp_path: Path) -> None:
    """A test twin matching more terms never outranks a production match."""
    index = build_index(tmp_path)
    add_file(
        index,
        "app/store.py",
        [make_symbol("py:prod", "index_writer", "app/store.py", line=1)],
    )
    add_file(
        index,
        "tests/test_store.py",
        [make_symbol("py:test", "symbol_index_writer", "tests/test_store.py", line=1)],
    )
    payload = _orient(index, tmp_path, terms=("symbol", "index", "writer"))

    names = _match_names(payload)
    assert names.index("index_writer") < names.index("symbol_index_writer")


def test_prefix_outranks_substring(tmp_path: Path) -> None:
    index = build_index(tmp_path)
    add_file(
        index,
        "app/one.py",
        [make_symbol("py:prefix", "service_builder", "app/one.py", line=1)],
    )
    add_file(
        index,
        "app/two.py",
        [make_symbol("py:sub", "microservice", "app/two.py", line=1)],
    )
    payload = _orient(index, tmp_path, terms=("service",))

    names = _match_names(payload)
    assert names.index("service_builder") < names.index("microservice")


def test_crowded_term_reports_count_and_ranks_exact_hits_first(tmp_path: Path) -> None:
    """Crowding still reports; exact leads; whole-subtoken hits are kept, not dropped."""
    index = build_index(tmp_path)
    swarm = [
        make_symbol(f"py:handler-{i}", f"handler_{i:02d}", "app/handlers.py", line=i + 1)
        for i in range(30)
    ]
    add_file(index, "app/handlers.py", swarm)
    add_file(index, "app/exact.py", [make_symbol("py:exact", "handler", "app/exact.py", line=1)])

    payload = _orient(index, tmp_path, terms=("handler",))

    crowded = payload["crowded_terms"]
    assert isinstance(crowded, dict)
    assert crowded["handler"] == 31
    names = _match_names(payload)
    assert names[0] == "handler"
    # `handler` is a whole subtoken of every swarm name, so the crowd stays
    # reachable behind the exact hit instead of being suppressed outright.
    assert all(name == "handler" or name.startswith("handler_") for name in names)
    assert "unmatched_terms" not in payload


def test_crowded_term_still_drops_mid_word_substring_hits(tmp_path: Path) -> None:
    """Only word-boundary evidence survives crowding; containment alone does not."""
    index = build_index(tmp_path)
    swarm = [
        make_symbol(f"py:handler-{i}", f"handler_{i:02d}", "app/handlers.py", line=i + 1)
        for i in range(30)
    ]
    add_file(index, "app/handlers.py", swarm)
    add_file(
        index,
        "app/mid.py",
        [make_symbol("py:mid", "prehandlerish", "app/mid.py", line=1)],
    )

    payload = _orient(index, tmp_path, terms=("handler",))

    assert "prehandlerish" not in _match_names(payload)
    crowded = payload["crowded_terms"]
    assert isinstance(crowded, dict)
    # The mid-word hit still counts toward crowding; it is only excluded from matches.
    assert crowded["handler"] == 31


def test_crowded_channel_candidates_never_displace_exact_matches(tmp_path: Path) -> None:
    """Unit-level port of the offline displacement gate: the channel adds, never evicts."""
    index = build_index(tmp_path)
    add_file(
        index,
        "app/gold.py",
        [make_symbol("py:gold", "resolve_workspace", "app/gold.py", line=1)],
    )
    swarm = [
        make_symbol(f"py:h-{i:02d}", f"handler_{i:02d}", "app/handlers.py", line=i + 1)
        for i in range(30)
    ]
    add_file(index, "app/handlers.py", swarm)

    payload = _orient(index, tmp_path, terms=("resolve_workspace", "handler"))

    names = _match_names(payload)
    assert names[0] == "resolve_workspace"


def test_unmatched_terms_are_reported_never_silently_dropped(tmp_path: Path) -> None:
    index = _twin_index(tmp_path)
    payload = _orient(index, tmp_path, terms=("build_service", "frobnicate"))

    assert payload["unmatched_terms"] == ["frobnicate"]


def test_literal_path_term_matches_files(tmp_path: Path) -> None:
    index = _twin_index(tmp_path)
    payload = _orient(index, tmp_path, terms=("app/service.py",))

    files = payload["files"]
    assert isinstance(files, list)
    assert "app/service.py" in files
    names = _match_names(payload)
    assert "build_service" in names


def test_path_scope_filters_candidates(tmp_path: Path) -> None:
    index = _twin_index(tmp_path)
    payload = _orient(index, tmp_path, terms=("build_service",), path_scope="tests")

    matches = payload["matches"]
    files = payload["files"]
    assert isinstance(matches, list)
    assert isinstance(files, list)
    assert all(str(files[entry["f"]]).startswith("tests/") for entry in matches)
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    assert coverage["path_scope"] == "tests"


def test_empty_terms_return_repository_map_orientation(tmp_path: Path) -> None:
    index = build_index(tmp_path)
    hub = make_symbol("py:hub", "Hub", "app/core/hub.py", kind=SymbolKind.CLASS, line=1)
    add_file(index, "app/core/hub.py", [hub])
    for i in range(3):
        caller = make_symbol(f"py:c{i}", f"use_hub_{i}", f"app/callers/mod{i}.py", line=1)
        add_file(
            index,
            f"app/callers/mod{i}.py",
            [caller],
            [
                make_reference(
                    f"r{i}",
                    from_symbol_id=f"py:c{i}",
                    to_symbol_id="py:hub",
                    from_file_path=f"app/callers/mod{i}.py",
                    resolution=ResolutionMethod.EXACT,
                )
            ],
        )
    add_file(index, "main.py", [make_symbol("py:main", "main", "main.py", line=1)])

    payload = _orient(index, tmp_path, terms=())

    map_section = payload["map"]
    assert isinstance(map_section, dict)
    assert map_section["areas"]
    entrypoints = map_section["entrypoints"]
    assert isinstance(entrypoints, list)
    assert any(entry["n"] == "main" for entry in entrypoints)
    matches = payload["matches"]
    assert isinstance(matches, list)
    assert all(entry["m"] == "map" for entry in matches)
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    assert coverage["scope"] == "ranked-orientation"


def test_empty_index_is_explicit_not_a_silent_zero(tmp_path: Path) -> None:
    index = build_index(tmp_path)
    payload = _orient(index, tmp_path, terms=("anything",))

    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    assert coverage["reason"] == "empty-index"
    assert payload["matches"] == []


def test_more_than_twelve_terms_is_a_value_error(tmp_path: Path) -> None:
    index = build_index(tmp_path)
    with pytest.raises(ValueError, match="at most 12 terms"):
        orient_workspace(
            index,
            OrientRequest(terms=tuple(f"term{i}" for i in range(13))),
            workspace_root=tmp_path,
        )


def test_orientation_is_byte_deterministic(tmp_path: Path) -> None:
    index = _twin_index(tmp_path)
    request = OrientRequest(terms=("build_service", "app/service.py"))
    first = orient_workspace(index, request, workspace_root=tmp_path)
    second = orient_workspace(index, request, workspace_root=tmp_path)
    assert first == second


def test_tiny_budget_clamps_and_stays_bounded(tmp_path: Path) -> None:
    index = _twin_index(tmp_path)
    result = orient_workspace(
        index,
        OrientRequest(terms=("build_service",), token_budget=1),
        workspace_root=tmp_path,
    )
    payload = json.loads(result)
    assert len(result) <= 400 * CHARS_PER_TOKEN
    budget = payload["budget"]
    assert budget["budget_tokens"] == 400
