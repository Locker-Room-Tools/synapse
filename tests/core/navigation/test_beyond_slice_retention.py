"""Retention of relation groups whose sites lie beyond the source-slice boundary.

Defect: outgoing groups sorted by earliest site line and were popped from the end of
the list under budget pressure, so the callees an agent could NOT see in the returned
40-line slice were exactly the ones dropped first. Groups invisible in the slice now
lead the outgoing order, and the boundary derives from the fixed cap rather than the
budget-shortened window so the drop loop stays a fixed point.
"""

import json
from pathlib import Path
from typing import Any

from synapse.core.index import SymbolIndex, symbol_handle
from synapse.core.navigation import InspectRequest, inspect_symbols

from .builders import add_file, build_index, make_reference, make_symbol, sync_disk_hashes


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return workspace


def _write_source(workspace: Path, file_path: str, line_count: int) -> None:
    absolute = workspace / file_path
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text("\n".join(f"line {i}" for i in range(1, line_count + 1)), encoding="utf-8")


def _long_function_index(tmp_path: Path, visible_callees: int) -> tuple[SymbolIndex, Path]:
    """A 60-line function calling `visible_callees` early callees and one at line 55."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    main = make_symbol("py:main", "run_pipeline", "app/pipeline.py", line=1, end_line=60)
    relations = []
    callees = []
    for i in range(1, visible_callees + 1):
        callee = make_symbol(f"py:v{i:02d}", f"stage_{i:02d}", "app/stages.py", line=5 * i)
        callees.append(callee)
        relations.append(
            make_reference(
                f"r-v{i:02d}",
                from_symbol_id="py:main",
                to_symbol_id=f"py:v{i:02d}",
                from_file_path="app/pipeline.py",
                to_file_path="app/stages.py",
                line=1 + i,
            )
        )
    tail = make_symbol("py:tail", "flush_side_effects", "app/stages.py", line=90)
    callees.append(tail)
    relations.append(
        make_reference(
            "r-tail",
            from_symbol_id="py:main",
            to_symbol_id="py:tail",
            from_file_path="app/pipeline.py",
            to_file_path="app/stages.py",
            line=55,
        )
    )
    add_file(index, "app/pipeline.py", [main], relations)
    add_file(index, "app/stages.py", callees)
    _write_source(workspace, "app/pipeline.py", 60)
    _write_source(workspace, "app/stages.py", 95)
    return index, workspace


def _inspect(
    index: SymbolIndex,
    workspace: Path,
    token_budget: int = 2400,
) -> dict[str, Any]:
    sync_disk_hashes(index, workspace)
    result = inspect_symbols(
        index,
        InspectRequest(symbols=(symbol_handle("py:main"),), token_budget=token_budget),
        workspace_root=workspace,
    )
    payload = json.loads(result)
    assert isinstance(payload, dict)
    return payload


def test_beyond_slice_callee_leads_the_outgoing_order(tmp_path: Path) -> None:
    index, workspace = _long_function_index(tmp_path, visible_callees=3)

    payload = _inspect(index, workspace, token_budget=4000)

    callees = payload["symbols"][0]["callees"]
    assert callees[0]["n"] == "flush_side_effects"
    assert callees[0]["sites"][0]["l"] == 55
    assert [g["sites"][0]["l"] for g in callees[1:]] == [2, 3, 4]


def test_beyond_slice_callee_outlives_visible_callees_under_pressure(tmp_path: Path) -> None:
    index, workspace = _long_function_index(tmp_path, visible_callees=8)

    pressured = None
    for budget in range(500, 2400, 50):
        candidate = _inspect(index, workspace, token_budget=budget)
        if candidate["budget"].get("dropped", {}).get("callee-group"):
            pressured = candidate
            break
    assert pressured is not None, "no budget forced callee-group drops"

    survivors = [g["n"] for g in pressured["symbols"][0]["callees"]]
    assert "flush_side_effects" in survivors, "the invisible callee must be retained"


def test_boundary_uses_the_fixed_cap_not_the_shortened_source(tmp_path: Path) -> None:
    index, workspace = _long_function_index(tmp_path, visible_callees=3)

    request = InspectRequest(symbols=(symbol_handle("py:main"),), token_budget=900)
    first = inspect_symbols(index, request, workspace_root=workspace)
    second = inspect_symbols(index, request, workspace_root=workspace)
    assert first == second

    payload = json.loads(first)
    entry = payload["symbols"][0]
    if entry.get("src", {}).get("shortened"):
        callees = entry.get("callees", [])
        if callees:
            assert callees[0]["n"] == "flush_side_effects", (
                "ordering must not follow the budget-shortened window"
            )


def test_incoming_order_is_unchanged_by_the_boundary_component(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    target = make_symbol("py:target", "target_fn", "app/target.py", line=1, end_line=60)
    early = make_symbol("py:early", "early_caller", "app/callers.py", line=2)
    late = make_symbol("py:late", "late_caller", "app/callers.py", line=80)
    add_file(index, "app/target.py", [target])
    add_file(
        index,
        "app/callers.py",
        [early, late],
        [
            make_reference(
                "r-late",
                from_symbol_id="py:late",
                to_symbol_id="py:target",
                from_file_path="app/callers.py",
                line=81,
            ),
            make_reference(
                "r-early",
                from_symbol_id="py:early",
                to_symbol_id="py:target",
                from_file_path="app/callers.py",
                line=3,
            ),
        ],
    )
    _write_source(workspace, "app/target.py", 60)
    _write_source(workspace, "app/callers.py", 85)

    result = inspect_symbols(
        index,
        InspectRequest(symbols=(symbol_handle("py:target"),), token_budget=2400),
        workspace_root=workspace,
    )
    callers = json.loads(result)["symbols"][0]["callers"]
    assert [g["n"] for g in callers] == ["early_caller", "late_caller"]
