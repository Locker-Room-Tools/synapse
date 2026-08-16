"""Same-file sibling discoverability in inspect payloads.

Defect: a pure-MCP agent inspecting a module-level declaration had no route to its
same-file top-level sibling — no relation edge exists between siblings and module-level
symbols carry no parent container — while shell-using agents reached the sibling through
whole-file reads. The `siblings` roster closes that gap with bounded, parser-proven
declarations only.
"""

import json
from pathlib import Path
from typing import Any

from synapse.core.index import SymbolIndex, symbol_handle
from synapse.core.models import SymbolKind
from synapse.core.navigation import InspectRequest, inspect_symbols

from .builders import add_file, build_index, make_contains, make_symbol


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return workspace


def _write_source(workspace: Path, file_path: str, line_count: int) -> None:
    absolute = workspace / file_path
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text("\n".join(f"line {i}" for i in range(1, line_count + 1)), encoding="utf-8")


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


def _probe_repair_index(tmp_path: Path) -> tuple[SymbolIndex, Path]:
    """Two unrelated top-level functions in one file — the observed A/B gap shape."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    probe = make_symbol("py:probe", "handle_probe_reason", "core/integrity.py", line=2)
    repair = make_symbol("py:repair", "repair_symbol_rows", "core/integrity.py", line=20)
    add_file(index, "core/integrity.py", [probe, repair])
    _write_source(workspace, "core/integrity.py", 30)
    return index, workspace


def test_top_level_sibling_is_discoverable_from_inspect(tmp_path: Path) -> None:
    index, workspace = _probe_repair_index(tmp_path)

    payload = _inspect(index, workspace, (symbol_handle("py:probe"),))

    entry = payload["symbols"][0]
    siblings = entry["siblings"]
    assert siblings == [{"h": symbol_handle("py:repair"), "n": "repair_symbol_rows", "l": 20}]
    assert "siblings_omitted" not in entry

    follow_up = _inspect(index, workspace, (siblings[0]["h"],))
    assert follow_up["symbols"][0]["n"] == "repair_symbol_rows"


def test_sibling_roster_excludes_self_and_orders_by_start_line_then_id(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    late = make_symbol("py:z-late", "zeta", "app/util.py", line=30)
    early = make_symbol("py:a-early", "alpha", "app/util.py", line=2)
    middle = make_symbol("py:m-middle", "mid", "app/util.py", line=10)
    add_file(index, "app/util.py", [late, early, middle])
    _write_source(workspace, "app/util.py", 40)

    payload = _inspect(index, workspace, (symbol_handle("py:m-middle"),))

    entry = payload["symbols"][0]
    assert [s["n"] for s in entry["siblings"]] == ["alpha", "zeta"]
    assert [s["l"] for s in entry["siblings"]] == [2, 30]


def test_nested_symbol_gets_the_file_top_level_roster(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    service = make_symbol(
        "py:service", "Service", "app/service.py", kind=SymbolKind.CLASS, line=1, end_line=15
    )
    method = make_symbol(
        "py:method", "authenticate", "app/service.py", line=5, container_id="py:service"
    )
    helper = make_symbol("py:helper", "top_helper", "app/service.py", line=18)
    add_file(
        index,
        "app/service.py",
        [service, method, helper],
        [make_contains("py:service", "py:method", "app/service.py")],
    )
    _write_source(workspace, "app/service.py", 25)

    payload = _inspect(index, workspace, (symbol_handle("py:method"),))

    entry = payload["symbols"][0]
    assert entry["parent"]["n"] == "Service"
    assert [s["n"] for s in entry["siblings"]] == ["Service", "top_helper"]


def test_sibling_cap_and_omission_arithmetic(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    symbols = [
        make_symbol(f"py:f{i:02d}", f"fn_{i:02d}", "app/many.py", line=2 * i) for i in range(1, 16)
    ]
    add_file(index, "app/many.py", symbols)
    _write_source(workspace, "app/many.py", 40)

    payload = _inspect(index, workspace, (symbol_handle("py:f01"),), token_budget=4000)

    entry = payload["symbols"][0]
    assert len(entry["siblings"]) == 10
    assert entry["siblings_omitted"] == 4
    assert len(entry["siblings"]) + entry["siblings_omitted"] == 14


def test_sibling_projection_is_byte_deterministic(tmp_path: Path) -> None:
    index, workspace = _probe_repair_index(tmp_path)
    request = InspectRequest(symbols=(symbol_handle("py:probe"),), token_budget=2400)

    first = inspect_symbols(index, request, workspace_root=workspace)
    second = inspect_symbols(index, request, workspace_root=workspace)

    assert first == second


def test_siblings_drop_before_selected_source_with_honest_omission(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    wide = make_symbol("py:wide", "wide_fn", "app/heavy.py", line=1, end_line=40)
    siblings = [
        make_symbol(f"py:s{i:02d}", f"sibling_fn_{i:02d}", "app/heavy.py", line=50 + i)
        for i in range(1, 11)
    ]
    add_file(index, "app/heavy.py", [wide, *siblings])
    absolute = workspace / "app/heavy.py"
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text("\n".join(f"line {i} {'x' * 60}" for i in range(1, 70)), encoding="utf-8")

    generous = _inspect(index, workspace, (symbol_handle("py:wide"),), token_budget=4000)
    assert len(generous["symbols"][0]["siblings"]) == 10

    pressured = _inspect(index, workspace, (symbol_handle("py:wide"),), token_budget=500)

    entry = pressured["symbols"][0]
    dropped = pressured["budget"].get("dropped", {})
    assert dropped.get("sibling"), "budget pressure must record sibling drops"
    kept = len(entry.get("siblings", []))
    assert kept + entry["siblings_omitted"] == 10
    assert "src" in entry, "the roster must never cost the selected source"


def test_wire_stays_bounded_with_rosters_at_every_budget(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    symbols = [
        make_symbol(f"py:g{i:02d}", f"gadget_{i:02d}", "app/wide.py", line=3 * i)
        for i in range(1, 13)
    ]
    add_file(index, "app/wide.py", symbols)
    _write_source(workspace, "app/wide.py", 60)

    for budget in (500, 2400, 4000):
        wire = inspect_symbols(
            index,
            InspectRequest(symbols=(symbol_handle("py:g01"),), token_budget=budget),
            workspace_root=workspace,
        )
        assert len(wire) <= budget * 4
