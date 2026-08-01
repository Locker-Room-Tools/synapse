"""Adversarial inputs: the hard character cap and valid JSON always hold."""

import json
from pathlib import Path

from synapse.core.index import SymbolIndex, symbol_handle
from synapse.core.models import ResolutionMethod
from synapse.core.navigation import (
    InspectRequest,
    OrientRequest,
    inspect_symbols,
    orient_workspace,
)
from synapse.core.navigation.budget import CHARS_PER_TOKEN
from tests.core.navigation.builders import add_file, build_index, make_reference, make_symbol


def _assert_bounded(result: str, token_budget: int) -> dict[str, object]:
    assert len(result) <= token_budget * CHARS_PER_TOKEN
    payload = json.loads(result)
    assert isinstance(payload, dict)
    return payload


def _wide_index(tmp_path: Path, *, name_length: int = 8, files: int = 40) -> SymbolIndex:
    index = build_index(tmp_path)
    for i in range(files):
        long_name = f"wide_symbol_{i:03d}_" + "x" * name_length
        path = f"app/deep/nested/pkg{i:03d}/module_{i:03d}.py"
        symbol = make_symbol(f"py:wide-{i:03d}", long_name, path, line=1)
        relations = [
            make_reference(
                f"r-{i:03d}-{j}",
                from_symbol_id=f"py:wide-{i:03d}",
                to_symbol_id=f"py:wide-{(i + 1) % files:03d}",
                from_file_path=path,
                resolution=ResolutionMethod.EXACT,
                line=j + 1,
            )
            for j in range(6)
        ]
        add_file(index, path, [symbol], relations)
    return index


def test_orient_minimum_budget_on_a_wide_graph(tmp_path: Path) -> None:
    index = _wide_index(tmp_path, name_length=60)
    result = orient_workspace(
        index,
        OrientRequest(terms=("wide_symbol",), token_budget=400),
        workspace_root=tmp_path,
    )
    _assert_bounded(result, 400)


def test_orient_maximum_budget_stays_bounded(tmp_path: Path) -> None:
    index = _wide_index(tmp_path)
    result = orient_workspace(
        index,
        OrientRequest(terms=("wide_symbol", "module", "app"), token_budget=1200),
        workspace_root=tmp_path,
    )
    _assert_bounded(result, 1200)


def test_orient_unicode_and_json_escaping_content(tmp_path: Path) -> None:
    index = build_index(tmp_path)
    tricky_name = 'опасный_"symbol"\\n\t✓'
    add_file(index, "app/tricky.py", [make_symbol("py:tricky", tricky_name, "app/tricky.py")])
    result = orient_workspace(
        index,
        OrientRequest(terms=(tricky_name, "об" + "ъ" * 120), token_budget=400),
        workspace_root=tmp_path,
    )
    payload = _assert_bounded(result, 400)
    assert payload["matches"] or payload["unmatched_terms"]


def test_inspect_minimum_budget_on_a_wide_graph(tmp_path: Path) -> None:
    index = _wide_index(tmp_path, name_length=60)
    handles = tuple(symbol_handle(f"py:wide-{i:03d}") for i in range(8))
    result = inspect_symbols(
        index,
        InspectRequest(symbols=handles, token_budget=500),
        workspace_root=tmp_path,
    )
    payload = _assert_bounded(result, 500)
    budget = payload.get("budget") or payload.get("truncation")
    assert isinstance(budget, dict)


def test_inspect_public_cap_budget_on_a_wide_graph(tmp_path: Path) -> None:
    index = _wide_index(tmp_path)
    handles = tuple(symbol_handle(f"py:wide-{i:03d}") for i in range(8))
    result = inspect_symbols(
        index,
        InspectRequest(symbols=handles, token_budget=4000),
        workspace_root=tmp_path,
    )
    payload = _assert_bounded(result, 4000)
    symbols = payload["symbols"]
    assert isinstance(symbols, list)
    assert symbols


def test_inspect_very_long_stable_ids_in_missing(tmp_path: Path) -> None:
    index = build_index(tmp_path)
    add_file(index, "app/one.py", [make_symbol("py:one", "alpha", "app/one.py")])
    long_id = "python:" + "a/" * 200 + "module.py:function:ghost:1"
    result = inspect_symbols(
        index,
        InspectRequest(symbols=(symbol_handle("py:one"), long_id), token_budget=500),
        workspace_root=tmp_path,
    )
    _assert_bounded(result, 500)


def test_both_calls_are_deterministic_under_pressure(tmp_path: Path) -> None:
    index = _wide_index(tmp_path)
    orient_request = OrientRequest(terms=("wide_symbol",), token_budget=400)
    inspect_request = InspectRequest(
        symbols=tuple(symbol_handle(f"py:wide-{i:03d}") for i in range(8)),
        token_budget=500,
    )
    assert orient_workspace(index, orient_request, workspace_root=tmp_path) == orient_workspace(
        index, orient_request, workspace_root=tmp_path
    )
    assert inspect_symbols(index, inspect_request, workspace_root=tmp_path) == inspect_symbols(
        index, inspect_request, workspace_root=tmp_path
    )
