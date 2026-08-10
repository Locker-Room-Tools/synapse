"""Classification-invariant projection: selected source survives call reclassification.

Iteration 7.2 exposed the regression pinned here: labelling call syntax moved relation
groups from neutral `refs_in`/`refs_out` (droppable to zero) into call-proven
`callers`/`callees` (floored), so an otherwise identical default-budget inspection
could lose every selected symbol's source. These tests exercise the SAME stored graph
under both classifications — a language advertising no call kinds versus one proving
calls — and require that source retention, handle round-trips, hard bounds, and
coverage accounting hold in both.
"""

import json
from pathlib import Path

import pytest

from synapse.core.index import SymbolIndex, symbol_handle
from synapse.core.navigation import InspectRequest, inspect_symbols
from synapse.core.navigation.budget import CHARS_PER_TOKEN
from tests.core.navigation.builders import (
    add_file,
    build_index,
    make_reference,
    make_symbol,
)

ANCHORS = ("handler_a", "handler_b", "handler_c")
CALLERS_PER_ANCHOR = 12
BODY_LINES = 36


def _relabel_workspace(index: SymbolIndex, language: str) -> None:
    """Force every indexed file's language; `add_file` always records Python."""
    with index.transaction() as connection:
        connection.execute("UPDATE files SET language = ?", (language,))
        connection.execute("UPDATE symbols SET language = ?", (language,))


def _write_body(workspace: Path, file_path: str, lines: int) -> None:
    absolute = workspace / file_path
    absolute.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"line {i:02d} " + "x" * 66 for i in range(1, lines + 1))
    absolute.write_text(body, encoding="utf-8")


def _pressured_index(tmp_path: Path, *, language: str) -> tuple[SymbolIndex, Path]:
    """Three long anchors with enough relations to pressure the default budget.

    Every reference stores `usage_kind="invocation"` in both classifications — the
    stored provenance is identical; only the advertised language coverage differs.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    index = build_index(tmp_path)

    helpers = [
        make_symbol(f"py:helper_{i}", f"helper_{i}", f"app/shared/helper_{i}.py", line=2)
        for i in range(2)
    ]
    for helper in helpers:
        add_file(index, helper.file_path, [helper])

    for a_index, name in enumerate(ANCHORS):
        anchor_path = f"app/area{a_index}/{name}.py"
        anchor = make_symbol(f"py:{name}", name, anchor_path, line=1, end_line=BODY_LINES)
        outgoing = [
            make_reference(
                f"r-out-{name}-{helper.name}",
                from_symbol_id=anchor.id,
                to_symbol_id=helper.id,
                from_file_path=anchor_path,
                to_file_path=helper.file_path,
                line=3 + i,
            )
            for i, helper in enumerate(helpers)
        ]
        add_file(index, anchor_path, [anchor], outgoing)
        _write_body(workspace, anchor_path, BODY_LINES)
        for c_index in range(CALLERS_PER_ANCHOR):
            caller_path = f"app/callers/{name}_c{c_index}.py"
            caller = make_symbol(f"py:{name}_c{c_index}", f"{name}_c{c_index}", caller_path, line=2)
            add_file(
                index,
                caller_path,
                [caller],
                [
                    make_reference(
                        f"r-in-{name}-{c_index}",
                        from_symbol_id=caller.id,
                        to_symbol_id=anchor.id,
                        from_file_path=caller_path,
                        line=4,
                    )
                ],
            )

    if language != "python":
        _relabel_workspace(index, language)
    return index, workspace


def _inspect_wire(
    index: SymbolIndex,
    workspace: Path,
    symbols: tuple[str, ...],
    token_budget: int = 2400,
) -> str:
    return inspect_symbols(
        index,
        InspectRequest(symbols=symbols, token_budget=token_budget),
        workspace_root=workspace,
    )


def _anchor_handles() -> tuple[str, ...]:
    return tuple(symbol_handle(f"py:{name}") for name in ANCHORS)


# `go` advertises no call kinds, so identical stored invocations stay neutral there.
CLASSIFICATIONS = ("python", "go")


@pytest.mark.parametrize("language", CLASSIFICATIONS)
def test_selected_source_survives_default_budget(tmp_path: Path, language: str) -> None:
    """Every selected anchor keeps a readable source slice under the default budget."""
    index, workspace = _pressured_index(tmp_path, language=language)
    payload = json.loads(_inspect_wire(index, workspace, _anchor_handles()))

    coverage = payload["coverage"]
    omitted = set(coverage.get("source_omitted") or [])
    for entry in payload["symbols"]:
        src = entry.get("src")
        assert isinstance(src, dict), f"{entry.get('n')} lost its source ({language})"
        assert src["text"], entry.get("n")
        assert entry["h"] not in omitted
    assert not omitted


def test_reclassification_moves_groups_without_strengthening(tmp_path: Path) -> None:
    """The same edges surface as callers when advertised, refs when not — verbatim."""
    neutral_index, neutral_ws = _pressured_index(tmp_path / "neutral", language="go")
    call_index, call_ws = _pressured_index(tmp_path / "call", language="python")

    neutral = json.loads(_inspect_wire(neutral_index, neutral_ws, _anchor_handles()))
    call = json.loads(_inspect_wire(call_index, call_ws, _anchor_handles()))

    for neutral_entry, call_entry in zip(neutral["symbols"], call["symbols"], strict=True):
        assert not neutral_entry.get("callers")
        assert neutral_entry.get("refs_in")
        assert call_entry.get("callers")
        assert not call_entry.get("refs_in")
        # Best-evidence-first ordering is shared, so the head groups hold the same
        # endpoint and the same stored provenance — classification never strengthens.
        neutral_head = neutral_entry["refs_in"][0]
        call_head = call_entry["callers"][0]
        assert neutral_head["n"] == call_head["n"]

        # `f` is a payload-local file-table index; the stored provenance triple and
        # line are what must be identical across classifications.
        def _provenance(sites: list[dict[str, object]]) -> list[dict[str, object]]:
            return [{k: v for k, v in site.items() if k != "f"} for site in sites]

        assert _provenance(neutral_head["sites"]) == _provenance(call_head["sites"])
        for site in call_head["sites"]:
            assert site["res"] == "exact"
            assert site["conf"] == "high"
            assert site["use"] == "invocation"


@pytest.mark.parametrize("language", CLASSIFICATIONS)
def test_wire_stays_bounded_and_byte_deterministic(tmp_path: Path, language: str) -> None:
    index, workspace = _pressured_index(tmp_path, language=language)

    first = _inspect_wire(index, workspace, _anchor_handles())
    second = _inspect_wire(index, workspace, _anchor_handles())

    assert first == second
    assert len(first) <= 2400 * CHARS_PER_TOKEN
    assert json.loads(first)["symbols"]


@pytest.mark.parametrize("language", CLASSIFICATIONS)
def test_a_relation_handle_round_trips_in_both_classifications(
    tmp_path: Path, language: str
) -> None:
    """At least one usable relation handle survives and works as follow-up input."""
    index, workspace = _pressured_index(tmp_path, language=language)
    payload = json.loads(_inspect_wire(index, workspace, _anchor_handles()))

    relation_key = "callers" if language == "python" else "refs_in"
    groups = payload["symbols"][0][relation_key]
    assert groups, f"no {relation_key} groups survived ({language})"
    follow_up = groups[0].get("h")
    assert isinstance(follow_up, str)

    second = json.loads(_inspect_wire(index, workspace, (follow_up,)))
    assert not second.get("missing")
    assert second["symbols"][0]["h"] == follow_up


@pytest.mark.parametrize("language", CLASSIFICATIONS)
def test_coverage_stays_internally_consistent(tmp_path: Path, language: str) -> None:
    """Omission counters and source causes agree with the entries after degradation."""
    index, workspace = _pressured_index(tmp_path, language=language)
    payload = json.loads(_inspect_wire(index, workspace, _anchor_handles()))
    coverage = payload["coverage"]

    totals = sum(entry["in_total"] + entry["out_total"] for entry in payload["symbols"])
    assert coverage["relations_returned"] + coverage["relations_omitted"] == totals

    shortened = set(coverage.get("source_shortened") or [])
    for entry in payload["symbols"]:
        src = entry.get("src")
        assert isinstance(src, dict)
        if entry["h"] in shortened:
            assert src.get("shortened") is True
        else:
            assert "shortened" not in src
    assert not coverage.get("source_omitted")


def test_eight_anchor_pathological_budget_stays_honest(tmp_path: Path) -> None:
    """Near the symbol maximum the projection may omit source, but says so exactly."""
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    index = build_index(tmp_path)
    handles = []
    for i in range(8):
        path = f"app/wide/body_{i}.py"
        symbol = make_symbol(f"py:wide_{i}", f"wide_{i}", path, line=1, end_line=50)
        add_file(index, path, [symbol])
        _write_body(workspace, path, 50)
        handles.append(symbol_handle(symbol.id))

    first = _inspect_wire(index, workspace, tuple(handles), token_budget=500)
    second = _inspect_wire(index, workspace, tuple(handles), token_budget=500)

    assert first == second
    assert len(first) <= 500 * CHARS_PER_TOKEN
    coverage = json.loads(first)["coverage"]
    omitted = set(coverage.get("source_omitted") or [])
    assert omitted, "the pathological case must reach honest source omission"
    assert not omitted & set(coverage.get("source_truncated") or [])
    assert not omitted & set(coverage.get("source_shortened") or [])
