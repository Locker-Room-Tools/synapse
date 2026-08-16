"""Deterministic navigation metrics over a synthetic, repository-neutral workspace.

The gold workflow must reach the expected evidence in one `synapse_orient` plus one
batched `synapse_inspect`, with no whole-file read and no grep. Every gate here is
computed from the wire payloads, so the numbers reported are the ones an agent sees.

Names and paths are generic on purpose: no benchmark-specific vocabulary, no
repository-specific heuristics.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from synapse.core.index import SymbolIndex
from synapse.core.indexing import index_workspace
from synapse.core.navigation import (
    INSPECT_DEFAULT_TOKEN_BUDGET,
    ORIENT_DEFAULT_TOKEN_BUDGET,
    InspectRequest,
    OrientRequest,
    estimate_tokens,
    inspect_symbols,
    orient_workspace,
)
from synapse.core.workspace import db_path

# A production package, a test twin, a lexical decoy, a cross-language caller, an
# unresolved call, and a short structural path — the shapes navigation must separate.
_FILES = {
    "app/store.py": (
        "class Base:\n    pass\n\n\n"
        "class Repository(Base):\n"
        "    def save(self, record):\n        return record\n\n"
        "    def load(self, key):\n        return key\n"
    ),
    "app/factory.py": (
        "from app.store import Repository\n\n\n"
        "def open_repository() -> Repository:\n    return Repository()\n\n\n"
        "def sync_records():\n    return open_repository().save('r')\n"
    ),
    "app/service.py": (
        "from app.factory import open_repository\n\n\n"
        "def handle_request(payload):\n    return open_repository()\n\n\n"
        "def broken():\n    return missing_helper()\n"
    ),
    # Shares the "repo" stem without being the target.
    "app/reporting.py": "def repository_report():\n    return 'report'\n",
    "app/Adapter.cs": (
        "namespace App;\npublic class Adapter\n{\n"
        "    public void Run(Adapter other) { other.Ping(); }\n"
        "    public void Ping() { }\n"
        "}\n"
    ),
    "tests/test_service.py": (
        "from app.service import handle_request\n\n\n"
        "def handle_request_twin():\n    return 1\n\n\n"
        "def test_handle_request_works():\n    assert handle_request(1)\n"
    ),
    "config/settings.py": "# deployment notes only\n",
}

# What a correct two-call workflow must surface, stated independently of the payload.
_GOLD_SYMBOLS = {"Repository", "open_repository", "sync_records", "save"}
_GOLD_FILES = {"app/store.py", "app/factory.py"}
_GOLD_CALL_EDGES = {("sync_records", "open_repository"), ("sync_records", "save")}


@pytest.fixture
def metrics_workspace(tmp_path: Path) -> tuple[Path, SymbolIndex]:
    workspace = tmp_path / "workspace"
    for relative_path, source in _FILES.items():
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    index_workspace(workspace)
    return workspace, SymbolIndex(db_path(workspace))


def _handles(payload: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    for section in ("matches", "weak"):
        for entry in payload.get(section) or []:
            found.setdefault(str(entry["n"]), str(entry["h"]))
    return found


def _two_call_transcript(
    workspace: Path, index: SymbolIndex
) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    """The whole supported workflow: one orientation, one batched inspection."""
    orient_wire = orient_workspace(
        index,
        OrientRequest(terms=("Repository", "open_repository", "sync_records", "save")),
        workspace_root=workspace,
    )
    orient_payload = json.loads(orient_wire)
    handles = _handles(orient_payload)
    selected = tuple(handles[name] for name in sorted(_GOLD_SYMBOLS) if name in handles)
    inspect_wire = inspect_symbols(
        index, InspectRequest(symbols=selected), workspace_root=workspace
    )
    return orient_payload, orient_wire, json.loads(inspect_wire), inspect_wire


def test_gold_evidence_is_reached_in_exactly_two_calls(
    metrics_workspace: tuple[Path, SymbolIndex],
) -> None:
    """Calls-to-gold-evidence = 2. No third call, no whole-file read, no grep."""
    workspace, index = metrics_workspace
    orient_payload, _, inspect_payload, _ = _two_call_transcript(workspace, index)

    # Top-k recall: every gold symbol and file is addressable from one orientation.
    names = [str(entry["n"]) for entry in orient_payload["matches"]]
    assert set(names[:8]) >= _GOLD_SYMBOLS, f"symbol recall gap: {names[:8]}"
    assert set(orient_payload["files"][:5]) >= _GOLD_FILES

    # And one batched inspection returns every one of them with a body.
    inspected = {str(entry["n"]).split(".")[-1] for entry in inspect_payload["symbols"]}
    assert inspected >= _GOLD_SYMBOLS
    assert all("src" in entry for entry in inspect_payload["symbols"])


def test_call_classification_precision_is_total(
    metrics_workspace: tuple[Path, SymbolIndex],
) -> None:
    """Every projected caller/callee is a call site; no type position is promoted."""
    workspace, index = metrics_workspace
    _, _, inspect_payload, _ = _two_call_transcript(workspace, index)

    call_uses: set[str] = set()
    neutral_uses: set[str] = set()
    edges: set[tuple[str, str]] = set()
    for entry in inspect_payload["symbols"]:
        target = str(entry["n"]).split(".")[-1]
        for key in ("callers", "callees"):
            for group in entry.get(key) or []:
                for site in group["sites"]:
                    call_uses.add(str(site.get("use")))
                source = str(group.get("n", "")).split(".")[-1]
                if key == "callers":
                    edges.add((source, target))
        for key in ("refs_in", "refs_out"):
            for group in entry.get(key) or []:
                for site in group["sites"]:
                    neutral_uses.add(str(site.get("use")))

    # Precision: only call-proven kinds ever appear as callers/callees.
    assert call_uses <= {"invocation", "object-creation"}, call_uses
    # A base list is real evidence, and it is kept strictly out of the call sets.
    assert "base-type" in neutral_uses
    assert not neutral_uses & {"invocation", "object-creation"}
    # Structural-path reachability: the gold call edges are present.
    assert edges >= _GOLD_CALL_EDGES, f"missing call edges: {_GOLD_CALL_EDGES - edges}"


def test_production_ranks_before_its_test_twin(
    metrics_workspace: tuple[Path, SymbolIndex],
) -> None:
    """Production-before-test ranking, with tests still reachable when scoped."""
    workspace, index = metrics_workspace
    payload = json.loads(
        orient_workspace(index, OrientRequest(terms=("handle_request",)), workspace_root=workspace)
    )
    files = payload["files"]
    positions = [files[int(entry["f"])] for entry in payload["matches"]]

    assert positions[0] == "app/service.py"
    assert positions.index("app/service.py") < min(
        (i for i, path in enumerate(positions) if path.startswith("tests/")),
        default=len(positions),
    )


def test_trust_and_extraction_coverage_are_calibrated(
    metrics_workspace: tuple[Path, SymbolIndex],
) -> None:
    """Stored resolution is reported verbatim and coverage never claims completeness."""
    workspace, index = metrics_workspace
    _, _, inspect_payload, _ = _two_call_transcript(workspace, index)
    coverage = inspect_payload["coverage"]
    assert isinstance(coverage, dict)

    assert coverage["exhaustive"] is False
    languages = {str(entry["language"]): entry for entry in coverage["extraction"]}
    assert "python" in languages
    assert languages["python"]["completeness"] == "partial"
    assert languages["python"]["call_kinds"] == ["invocation"]

    resolutions = {
        str(site.get("res"))
        for entry in inspect_payload["symbols"]
        for key in ("callers", "callees", "refs_in", "refs_out")
        for group in entry.get(key) or []
        for site in group["sites"]
    }
    # Nothing is upgraded past what the resolver actually proved.
    assert resolutions <= {"exact", "scoped", "unique-name", "ambiguous", "unresolved"}


def test_fixed_caps_and_budget_omissions_are_reported_honestly(
    metrics_workspace: tuple[Path, SymbolIndex],
) -> None:
    """Every bound that shaped the answer is stated in the payload it shaped."""
    workspace, index = metrics_workspace
    orient_payload, _, inspect_payload, _ = _two_call_transcript(workspace, index)

    orient_coverage = orient_payload["coverage"]
    assert isinstance(orient_coverage, dict)
    assert set(orient_coverage["caps"]) == {
        "names",
        "crowded_names",
        "paths",
        "matches",
        "weak",
        "files",
    }
    assert (
        orient_coverage["returned"] + orient_coverage["omitted"] == (orient_coverage["discovered"])
    )

    inspect_coverage = inspect_payload["coverage"]
    assert isinstance(inspect_coverage, dict)
    assert inspect_coverage["relations_omitted"] >= 0
    assert inspect_payload["payload_complete"] is True


def test_schema_and_payload_token_costs_stay_within_the_contract(
    metrics_workspace: tuple[Path, SymbolIndex],
) -> None:
    """Orientation and inspection stay inside their advertised budgets."""
    workspace, index = metrics_workspace
    _, orient_wire, _, inspect_wire = _two_call_transcript(workspace, index)

    assert estimate_tokens(orient_wire) <= ORIENT_DEFAULT_TOKEN_BUDGET
    assert len(orient_wire) <= ORIENT_DEFAULT_TOKEN_BUDGET * 4
    assert estimate_tokens(inspect_wire) <= INSPECT_DEFAULT_TOKEN_BUDGET
    assert len(inspect_wire) <= INSPECT_DEFAULT_TOKEN_BUDGET * 4


def test_the_two_call_transcript_is_byte_deterministic(
    metrics_workspace: tuple[Path, SymbolIndex],
) -> None:
    """One index state produces one transcript, so the metrics above are reproducible."""
    workspace, index = metrics_workspace
    first = _two_call_transcript(workspace, index)
    second = _two_call_transcript(workspace, index)

    assert first[1] == second[1]
    assert first[3] == second[3]
