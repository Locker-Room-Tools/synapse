"""Batch inspection behavior over hand-built indexes."""

import json
from pathlib import Path
from typing import Any

import pytest

from synapse.core.index import SymbolIndex, symbol_handle
from synapse.core.models import Confidence, ResolutionMethod, SymbolKind
from synapse.core.navigation import InspectRequest, inspect_symbols
from synapse.core.navigation.budget import CHARS_PER_TOKEN
from tests.core.navigation.builders import (
    add_file,
    build_index,
    make_contains,
    make_reference,
    make_symbol,
)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    return workspace


def _write_source(workspace: Path, file_path: str, line_count: int) -> None:
    absolute = workspace / file_path
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text("\n".join(f"line {i}" for i in range(1, line_count + 1)), encoding="utf-8")


def _service_index(tmp_path: Path) -> tuple[SymbolIndex, Path]:
    """One service method with an exact caller, a scoped callee, and a non-call ref."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    service = make_symbol(
        "py:service",
        "Service",
        "app/service.py",
        kind=SymbolKind.CLASS,
        line=1,
        end_line=30,
    )
    method = make_symbol(
        "py:method",
        "authenticate",
        "app/service.py",
        line=5,
        end_line=12,
        container_id="py:service",
        qualified_name="Service.authenticate",
        signature="def authenticate(self, user):",
    )
    helper = make_symbol("py:helper", "hash_password", "app/crypto.py", line=2)
    caller = make_symbol("py:caller", "handle_login", "app/handlers.py", line=3)
    mentioner = make_symbol("py:mention", "DocsIndex", "app/docs.py", kind=SymbolKind.CLASS, line=8)
    add_file(
        index,
        "app/service.py",
        [service, method],
        [
            make_contains("py:service", "py:method", "app/service.py"),
            make_reference(
                "r-callee",
                from_symbol_id="py:method",
                to_symbol_id="py:helper",
                from_file_path="app/service.py",
                to_file_path="app/crypto.py",
                resolution=ResolutionMethod.SCOPED,
                line=8,
            ),
        ],
    )
    add_file(index, "app/crypto.py", [helper])
    add_file(
        index,
        "app/handlers.py",
        [caller],
        [
            make_reference(
                "r-caller",
                from_symbol_id="py:caller",
                to_symbol_id="py:method",
                from_file_path="app/handlers.py",
                resolution=ResolutionMethod.EXACT,
                line=4,
            )
        ],
    )
    add_file(
        index,
        "app/docs.py",
        [mentioner],
        [
            make_reference(
                "r-mention",
                from_symbol_id="py:mention",
                to_symbol_id="py:method",
                from_file_path="app/docs.py",
                resolution=ResolutionMethod.SCOPED,
                line=9,
                usage_kind="mention",
                confidence=Confidence.MEDIUM,
            )
        ],
    )
    _write_source(workspace, "app/service.py", 30)
    return index, workspace


def _inspect(
    index: SymbolIndex,
    workspace: Path,
    symbols: tuple[str, ...],
    token_budget: int = 2400,
) -> dict[str, Any]:
    result = inspect_symbols(
        index,
        InspectRequest(symbols=symbols, token_budget=token_budget),
        workspace_root=workspace,
    )
    payload = json.loads(result)
    assert isinstance(payload, dict)
    return payload


def test_inspection_returns_definition_relations_and_source_verbatim(tmp_path: Path) -> None:
    index, workspace = _service_index(tmp_path)
    payload = _inspect(index, workspace, (symbol_handle("py:method"),))

    symbols = payload["symbols"]
    assert isinstance(symbols, list)
    entry = symbols[0]
    assert entry["h"] == symbol_handle("py:method")
    assert entry["n"] == "Service.authenticate"
    assert entry["sig"] == "def authenticate(self, user):"
    assert entry["parent"]["n"] == "Service"
    assert entry["lines"] == [5, 12]

    assert entry["siblings"] == [{"h": symbol_handle("py:service"), "n": "Service", "l": 1}]

    callers = entry["callers"]
    assert len(callers) == 1
    assert callers[0]["n"] == "handle_login"
    assert callers[0]["l"] == 3
    site = callers[0]["sites"][0]
    assert site["res"] == "exact"
    assert site["conf"] == "high"
    assert site["use"] == "invocation"
    assert site["l"] == 4

    callees = entry["callees"]
    assert len(callees) == 1
    assert callees[0]["n"] == "hash_password"
    assert callees[0]["l"] == 2
    assert callees[0]["sites"][0]["res"] == "scoped"

    # A `mention` is not a call kind Python advertises, so it stays neutral evidence
    # even though its far endpoint is a callable declaration.
    refs_in = entry["refs_in"]
    assert len(refs_in) == 1
    assert refs_in[0]["sites"][0]["use"] == "mention"
    assert refs_in[0]["sites"][0]["conf"] == "medium"

    assert entry["in_total"] == 2
    assert entry["out_total"] == 1
    src = entry["src"]
    assert src["lines"] == [5, 12]
    assert src["text"].startswith("line 5")
    assert src["truncated"] is False

    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    assert coverage["scope"] == "selected-symbol-one-hop"
    assert coverage["selected"] == 1
    assert coverage["requested"] == 1
    assert payload["payload_complete"] is True


def test_stable_ids_and_handles_are_both_accepted(tmp_path: Path) -> None:
    index, workspace = _service_index(tmp_path)
    payload = _inspect(index, workspace, ("py:method", symbol_handle("py:helper")))

    symbols = payload["symbols"]
    assert isinstance(symbols, list)
    assert [entry["h"] for entry in symbols] == [
        symbol_handle("py:method"),
        symbol_handle("py:helper"),
    ]


def test_missing_handles_are_reported_not_invented(tmp_path: Path) -> None:
    index, workspace = _service_index(tmp_path)
    ghost = "s_" + "A" * 22
    payload = _inspect(index, workspace, (symbol_handle("py:method"), ghost, "py:ghost-id"))

    assert payload["missing"] == [ghost, "py:ghost-id"]
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    assert coverage["selected"] == 1
    assert coverage["requested"] == 3


def test_unresolved_incoming_references_surface_as_hypotheses(tmp_path: Path) -> None:
    index, workspace = _service_index(tmp_path)
    add_file(
        index,
        "app/loose.py",
        [make_symbol("py:loose", "loose_caller", "app/loose.py", line=1)],
        [
            make_reference(
                "r-unresolved",
                from_symbol_id="py:loose",
                to_symbol_id=None,
                from_file_path="app/loose.py",
                to_name="authenticate",
                resolution=ResolutionMethod.UNRESOLVED,
                line=7,
            )
        ],
    )
    payload = _inspect(index, workspace, (symbol_handle("py:method"),))

    symbols = payload["symbols"]
    assert isinstance(symbols, list)
    hypotheses = symbols[0]["hypotheses"]
    assert len(hypotheses) == 1
    assert hypotheses[0]["res"] == "unresolved"
    files = payload["files"]
    assert isinstance(files, list)
    assert files[hypotheses[0]["f"]] == "app/loose.py"


def test_unresolved_outgoing_calls_keep_their_target_name(tmp_path: Path) -> None:
    index, workspace = _service_index(tmp_path)
    add_file(
        index,
        "app/extra.py",
        [make_symbol("py:extra", "extra", "app/extra.py", line=1)],
        [
            make_reference(
                "r-out-unresolved",
                from_symbol_id="py:method",
                to_symbol_id=None,
                from_file_path="app/service.py",
                to_name="mystery_call",
                resolution=ResolutionMethod.UNRESOLVED,
                line=11,
            )
        ],
    )
    payload = _inspect(index, workspace, (symbol_handle("py:method"),))

    symbols = payload["symbols"]
    assert isinstance(symbols, list)
    callees = symbols[0]["callees"]
    unresolved = [group for group in callees if group.get("n") == "mystery_call"]
    assert len(unresolved) == 1
    assert "h" not in unresolved[0]
    assert unresolved[0]["sites"][0]["res"] == "unresolved"


def test_group_and_site_caps_stay_visible(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    hub = make_symbol("py:hub", "hub", "app/hub.py", line=1, end_line=2)
    add_file(index, "app/hub.py", [hub])
    for i in range(15):
        caller_id = f"py:caller-{i:02d}"
        relations = [
            make_reference(
                f"r-{i:02d}-{j}",
                from_symbol_id=caller_id,
                to_symbol_id="py:hub",
                from_file_path=f"app/callers/mod{i:02d}.py",
                resolution=ResolutionMethod.EXACT,
                line=j + 1,
            )
            for j in range(5)
        ]
        add_file(
            index,
            f"app/callers/mod{i:02d}.py",
            [make_symbol(caller_id, f"caller_{i:02d}", f"app/callers/mod{i:02d}.py", line=1)],
            relations,
        )

    payload = _inspect(index, workspace, (symbol_handle("py:hub"),), token_budget=4000)

    symbols = payload["symbols"]
    assert isinstance(symbols, list)
    entry = symbols[0]
    callers = entry["callers"]
    assert len(callers) == 12
    assert all(len(group["sites"]) <= 3 for group in callers)
    assert all(group["more"] == 2 for group in callers)
    assert entry["in_total"] == 75
    assert entry["in_omitted"] == 75 - 12 * 3
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    assert coverage["relations_omitted"] > 0


def test_source_truncation_is_reported(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    wide = make_symbol("py:wide", "wide", "app/wide.py", line=1, end_line=90)
    add_file(index, "app/wide.py", [wide])
    _write_source(workspace, "app/wide.py", 90)

    payload = _inspect(index, workspace, (symbol_handle("py:wide"),))

    symbols = payload["symbols"]
    assert isinstance(symbols, list)
    src = symbols[0]["src"]
    assert src["truncated"] is True
    assert len(str(src["text"]).splitlines()) == 40
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    assert coverage["source_truncated"] == [symbol_handle("py:wide")]


def test_symbol_count_bounds_are_value_errors(tmp_path: Path) -> None:
    index, workspace = _service_index(tmp_path)
    with pytest.raises(ValueError, match="1-8 symbols"):
        inspect_symbols(index, InspectRequest(symbols=()), workspace_root=workspace)
    with pytest.raises(ValueError, match="1-8 symbols"):
        inspect_symbols(
            index,
            InspectRequest(symbols=tuple(f"py:{i}" for i in range(9))),
            workspace_root=workspace,
        )


def test_inspection_is_byte_deterministic(tmp_path: Path) -> None:
    index, workspace = _service_index(tmp_path)
    request = InspectRequest(symbols=(symbol_handle("py:method"), "py:helper"))
    first = inspect_symbols(index, request, workspace_root=workspace)
    second = inspect_symbols(index, request, workspace_root=workspace)
    assert first == second


def test_cross_area_caller_handle_survives_default_budget_and_round_trips(
    tmp_path: Path,
) -> None:
    """A returned relation handle is a first-class follow-up inspection input.

    The managed workflow closes open facets by inspecting handles taken from
    `callers`/`callees` groups, so a cross-area caller must keep its handle under the
    default budget and that handle must resolve in a second call.
    """
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    target = make_symbol("py:process", "process", "app/core/process.py", line=3)
    cross_caller = make_symbol("py:schedule", "schedule", "app/jobs/schedule.py", line=5)
    local_callers = [
        make_symbol(f"py:local{i}", f"local_caller_{i}", f"app/core/local{i}.py", line=2)
        for i in range(4)
    ]
    add_file(index, "app/core/process.py", [target])
    add_file(
        index,
        "app/jobs/schedule.py",
        [cross_caller],
        [
            make_reference(
                "r-cross",
                from_symbol_id="py:schedule",
                to_symbol_id="py:process",
                from_file_path="app/jobs/schedule.py",
                resolution=ResolutionMethod.EXACT,
                line=6,
            )
        ],
    )
    for i, caller in enumerate(local_callers):
        add_file(
            index,
            caller.file_path,
            [caller],
            [
                make_reference(
                    f"r-local{i}",
                    from_symbol_id=caller.id,
                    to_symbol_id="py:process",
                    from_file_path=caller.file_path,
                    resolution=ResolutionMethod.EXACT,
                    line=3,
                )
            ],
        )
    _write_source(workspace, "app/core/process.py", 10)
    _write_source(workspace, "app/jobs/schedule.py", 10)

    first = _inspect(index, workspace, (symbol_handle("py:process"),))
    callers = first["symbols"][0]["callers"]
    assert isinstance(callers, list)
    cross_groups = [group for group in callers if group.get("n") == "schedule"]
    assert cross_groups, "the cross-area caller group must survive the default budget"
    follow_up_handle = cross_groups[0].get("h")
    assert isinstance(follow_up_handle, str)

    second = _inspect(index, workspace, (follow_up_handle,))
    assert not second.get("missing")
    followed = second["symbols"][0]
    assert followed["n"] == "schedule"
    assert followed["h"] == follow_up_handle


def test_tight_budget_degrades_honestly(tmp_path: Path) -> None:
    index, workspace = _service_index(tmp_path)
    result = inspect_symbols(
        index,
        InspectRequest(
            symbols=(symbol_handle("py:method"), symbol_handle("py:service")),
            token_budget=1,
        ),
        workspace_root=workspace,
    )
    payload = json.loads(result)
    assert len(result) <= 500 * CHARS_PER_TOKEN
    if "payload_complete" in payload:
        assert payload["payload_complete"] is False or payload["budget"]["complete"] is True
