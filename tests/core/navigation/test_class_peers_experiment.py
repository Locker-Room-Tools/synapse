"""Invariants for the experimental additive class peer projection."""

import json
from pathlib import Path

import pytest

from synapse.core.index import symbol_handle
from synapse.core.models import SymbolKind
from synapse.core.navigation import inspection
from synapse.core.navigation.inspection import InspectRequest, inspect_symbols
from tests.core.navigation.builders import add_file, build_index, make_symbol


def test_additions_preserve_baseline_and_are_round_robin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = build_index(tmp_path)
    symbols = []
    selected = []
    for group in range(3):
        parent = f"class{group}"
        symbols.append(make_symbol(parent, parent, "app.py", kind=SymbolKind.CLASS))
        for number in range(7):
            key = f"{parent}.m{number}"
            symbols.append(
                make_symbol(
                    key,
                    key,
                    "app.py",
                    kind=SymbolKind.METHOD,
                    container_id=parent,
                    line=10 + number * 2,
                )
            )
        selected.append(symbol_handle(f"{parent}.m3"))
    add_file(index, "app.py", symbols)
    request = InspectRequest(tuple(selected), token_budget=4000)
    actual = json.loads(inspect_symbols(index, request, workspace_root=tmp_path))
    monkeypatch.setattr(inspection, "_add_class_peers", lambda _r, _s, baseline, _b: baseline)
    baseline = json.loads(inspect_symbols(index, request, workspace_root=tmp_path))
    assert sum(len(entry.get("class_peers", [])) for entry in actual["symbols"]) == 6
    for entry in actual["symbols"]:
        peers = entry.pop("class_peers")
        assert len(peers) == 2
        assert [peer["l"] for peer in peers] == [14, 18]
        assert entry.pop("class_peers_total") == 6
    actual["budget"]["estimated_tokens"] = baseline["budget"]["estimated_tokens"]
    assert actual == baseline


def test_tight_budget_returns_original_and_duplicates_are_filtered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = build_index(tmp_path)
    parent = make_symbol("class", "C", "app.py", kind=SymbolKind.CLASS)
    methods = [
        make_symbol(
            f"m{i}", f"m{i}", "app.py", kind=SymbolKind.METHOD, container_id="class", line=10 + i
        )
        for i in range(8)
    ]
    add_file(index, "app.py", [parent, *methods])
    request = InspectRequest((symbol_handle("m3"), symbol_handle("m4")), token_budget=4000)
    actual = json.loads(inspect_symbols(index, request, workspace_root=tmp_path))
    handles = [peer["h"] for entry in actual["symbols"] for peer in entry.get("class_peers", [])]
    assert len(handles) == len(set(handles))
    assert not set(handles) & {symbol_handle("m3"), symbol_handle("m4")}
    request = InspectRequest((symbol_handle("m3"),), token_budget=500)
    output = inspect_symbols(index, request, workspace_root=tmp_path)
    monkeypatch.setattr(inspection, "_add_class_peers", lambda _r, _s, baseline, _b: baseline)
    baseline = inspect_symbols(index, request, workspace_root=tmp_path)
    # When no addition fits, fallback must be byte-for-byte unchanged.
    if "class_peers" not in output:
        assert output == baseline
    assert len(output) <= 2000


def test_oversized_peer_is_skipped_and_full_payload_is_unchanged(tmp_path: Path) -> None:
    index = build_index(tmp_path)
    parent = make_symbol("C", "C", "app.py", kind=SymbolKind.CLASS)
    selected = make_symbol("m", "m", "app.py", kind=SymbolKind.METHOD, container_id="C", line=10)
    huge = make_symbol(
        "huge", "x" * 2100, "app.py", kind=SymbolKind.METHOD, container_id="C", line=11
    )
    small = make_symbol(
        "small", "small", "app.py", kind=SymbolKind.METHOD, container_id="C", line=12
    )
    add_file(index, "app.py", [parent, selected, huge, small])
    payload = {
        "symbols": [{"h": symbol_handle("m")}],
        "budget": {"budget_tokens": 500, "estimated_tokens": 0, "complete": True},
    }
    baseline = json.dumps(payload, separators=(",", ":"))
    with index.read_session() as reads:
        evidence = inspection._build_selected(reads, (symbol_handle("m"),), tmp_path)
        result = inspection._add_class_peers(reads, evidence.selected, baseline, 500)
        peers = json.loads(result)["symbols"][0]["class_peers"]
        assert [peer["h"] for peer in peers] == [symbol_handle("small")]
        payload["padding"] = ""
        padding = 2000 - len(json.dumps(payload, separators=(",", ":")))
        payload["padding"] = "z" * padding
        full = json.dumps(payload, separators=(",", ":"))
        assert len(full) == 2000
        assert inspection._add_class_peers(reads, evidence.selected, full, 500) == full
