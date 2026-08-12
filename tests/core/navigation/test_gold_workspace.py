"""Gold navigation scenarios through the real pipeline: parse, index, resolve.

A synthetic multi-file package with production code, a test twin, lexical
decoys, exact and scoped calls, an unresolved reference, and a short structural
path. The deterministic gates from the Iteration 7 contract are asserted here:
file/symbol recall, definition and reference precision, trust calibration,
edge reachability in two calls, production-before-test ordering, and budgets.
"""

import json
from pathlib import Path

import pytest

from synapse.core.index import SymbolIndex
from synapse.core.indexing import index_workspace
from synapse.core.navigation import (
    InspectRequest,
    OrientRequest,
    inspect_symbols,
    orient_workspace,
)
from synapse.core.navigation.budget import estimate_tokens
from synapse.core.workspace import db_path

_FILES = {
    "app/__init__.py": (
        "from app.store import Repository\nfrom app.factory import open_repository\n"
    ),
    "app/store.py": (
        "class Repository:\n"
        "    def save(self, record):\n"
        "        return record\n\n"
        "    def load(self, key):\n"
        "        return key\n\n"
        "class MemoryCache:\n"
        "    def save(self, record):\n"
        "        return record\n"
    ),
    "app/factory.py": (
        "from app.store import Repository\n\n"
        "def open_repository() -> Repository:\n"
        "    return Repository()\n\n"
        "def sync_records():\n"
        "    return open_repository().save('r')\n"
    ),
    "app/service.py": (
        "import functools\n"
        "from app.factory import open_repository\n\n"
        "@functools.cache\n"
        "def handle_request(payload):\n"
        "    return open_repository()\n\n"
        "def relay(cache):\n"
        "    return cache.load('k')\n\n"
        "def broken():\n"
        "    return missing_helper()\n"
    ),
    "main.py": ("from app import open_repository\n\ndef main():\n    return open_repository()\n"),
    # Lexical decoy: shares the "repository" stem without being the target.
    "app/reporting.py": (
        "def repository_report():\n    return 'report'\n\ndef open_report():\n    return 'r'\n"
    ),
    # Test twin: the production name redeclared under tests/.
    "tests/conftest.py": ("def handle_request(payload):\n    return payload\n"),
    "tests/test_service.py": (
        "from app.service import handle_request\n\n"
        "def test_handle_request_works():\n"
        "    assert handle_request(1)\n"
    ),
}


@pytest.fixture
def gold_workspace(tmp_path: Path) -> tuple[Path, SymbolIndex]:
    workspace = tmp_path / "workspace"
    for relative_path, source in _FILES.items():
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    index_workspace(workspace)
    return workspace, SymbolIndex(db_path(workspace))


def _orient(
    workspace: Path, index: SymbolIndex, terms: tuple[str, ...]
) -> tuple[dict[str, object], str]:
    result = orient_workspace(index, OrientRequest(terms=terms), workspace_root=workspace)
    payload = json.loads(result)
    assert isinstance(payload, dict)
    return payload, result


def _handles_by_name(payload: dict[str, object]) -> dict[str, str]:
    found: dict[str, str] = {}
    for section in ("matches", "weak"):
        entries = payload.get(section, [])
        assert isinstance(entries, list)
        for entry in entries:
            found.setdefault(str(entry["n"]), str(entry["h"]))
    return found


def test_top_files_and_symbols_recall_is_total(
    gold_workspace: tuple[Path, SymbolIndex],
) -> None:
    """Gate: top-5 file recall 1.0 and top-8 symbol recall 1.0 on the gold scenario."""
    workspace, index = gold_workspace
    payload, _ = _orient(
        workspace, index, ("open_repository", "Repository", "sync_records", "save")
    )

    files = payload["files"]
    assert isinstance(files, list)
    assert {"app/factory.py", "app/store.py"} <= set(files[:5])

    matches = payload["matches"]
    assert isinstance(matches, list)
    top_names = [str(entry["n"]) for entry in matches[:8]]
    assert {"open_repository", "Repository", "sync_records", "save"} <= set(top_names)


def test_exact_definition_precision_is_total(
    gold_workspace: tuple[Path, SymbolIndex],
) -> None:
    """Gate: every returned definition is the true declaration (precision 1.0)."""
    workspace, index = gold_workspace
    payload, _ = _orient(workspace, index, ("open_repository", "Repository"))
    handles = _handles_by_name(payload)

    result = inspect_symbols(
        index,
        InspectRequest(symbols=(handles["open_repository"], handles["Repository"])),
        workspace_root=workspace,
    )
    inspected = json.loads(result)
    files = inspected["files"]
    symbols = {str(entry["n"]): entry for entry in inspected["symbols"]}

    factory = symbols["open_repository"]
    assert files[factory["f"]] == "app/factory.py"
    assert factory["lines"][0] == 3
    repository = symbols["Repository"]
    assert files[repository["f"]] == "app/store.py"
    assert repository["lines"][0] == 1
    assert repository["k"] == "class"


def test_reference_precision_and_trust_calibration_are_total(
    gold_workspace: tuple[Path, SymbolIndex],
) -> None:
    """Gate: stored resolution classes are reported verbatim, never upgraded.

    The receiver-typed ``open_repository().save`` call resolves exact; plain
    Python name calls are conservatively ``unique-name`` and must stay that way.
    """
    workspace, index = gold_workspace
    payload, _ = _orient(workspace, index, ("save", "open_repository"))
    handles = _handles_by_name(payload)

    result = inspect_symbols(
        index,
        InspectRequest(symbols=(handles["save"], handles["open_repository"])),
        workspace_root=workspace,
    )
    inspected = json.loads(result)
    files = inspected["files"]
    symbols = {str(entry["n"]): entry for entry in inspected["symbols"]}

    save_entry = symbols["Repository.save"]
    assert files[save_entry["f"]] == "app/store.py"
    save_callers = {str(group["n"]): group for group in save_entry["callers"]}
    assert "sync_records" in save_callers
    exact_site = save_callers["sync_records"]["sites"][0]
    assert exact_site["res"] == "exact"

    factory_entry = symbols["open_repository"]
    caller_names = {str(group["n"]) for group in factory_entry["callers"]}
    assert {"main", "handle_request", "sync_records"} <= caller_names
    for group in factory_entry["callers"]:
        for site in group["sites"]:
            assert site["res"] == "unique-name"
    caller_files = {str(files[group["f"]]) for group in factory_entry["callers"]}
    assert "main.py" in caller_files
    assert "app/service.py" in caller_files


def test_gold_structural_path_is_reachable_in_two_calls(
    gold_workspace: tuple[Path, SymbolIndex],
) -> None:
    """Gate: orientation plus one inspection surfaces every gold edge; no grep."""
    workspace, index = gold_workspace
    payload, orient_wire = _orient(
        workspace, index, ("open_repository", "Repository", "sync_records", "save")
    )
    handles = _handles_by_name(payload)
    for needed in ("open_repository", "Repository", "sync_records", "save"):
        assert needed in handles, f"orientation must supply the {needed} handle"

    result = inspect_symbols(
        index,
        InspectRequest(
            symbols=(handles["open_repository"], handles["sync_records"], handles["Repository"])
        ),
        workspace_root=workspace,
    )
    inspected = json.loads(result)
    symbols = {str(entry["n"]): entry for entry in inspected["symbols"]}

    factory_callers = {str(group["n"]) for group in symbols["open_repository"]["callers"]}
    assert {"main", "handle_request", "sync_records"} <= factory_callers
    factory_callees = {str(group["n"]) for group in symbols["open_repository"]["callees"]}
    assert "Repository" in factory_callees
    sync_callees = {str(group["n"]) for group in symbols["sync_records"]["callees"]}
    assert any("save" in name for name in sync_callees)

    assert estimate_tokens(orient_wire) <= 800
    assert estimate_tokens(result) <= 2400


def test_production_declaration_ranks_before_its_test_twin(
    gold_workspace: tuple[Path, SymbolIndex],
) -> None:
    """Gate: the tests/ twin of a production name never outranks production."""
    workspace, index = gold_workspace
    payload, _ = _orient(workspace, index, ("handle_request",))

    matches = payload["matches"]
    files = payload["files"]
    assert isinstance(matches, list)
    assert isinstance(files, list)
    positions = {str(files[entry["f"]]): i for i, entry in enumerate(matches)}
    assert "app/service.py" in positions
    assert "tests/conftest.py" in positions
    assert positions["app/service.py"] < positions["tests/conftest.py"]


def test_tests_can_be_explicitly_scoped(
    gold_workspace: tuple[Path, SymbolIndex],
) -> None:
    workspace, index = gold_workspace
    payload, _ = _orient_scoped(workspace, index, ("handle_request",), "tests")
    matches = payload["matches"]
    files = payload["files"]
    assert isinstance(matches, list)
    assert isinstance(files, list)
    assert matches
    assert all(str(files[entry["f"]]).startswith("tests/") for entry in matches)


def _orient_scoped(
    workspace: Path, index: SymbolIndex, terms: tuple[str, ...], scope: str
) -> tuple[dict[str, object], str]:
    result = orient_workspace(
        index, OrientRequest(terms=terms, path_scope=scope), workspace_root=workspace
    )
    payload = json.loads(result)
    assert isinstance(payload, dict)
    return payload, result


def test_unresolved_reference_is_reported_never_resolved_by_guess(
    gold_workspace: tuple[Path, SymbolIndex],
) -> None:
    """Gate: the missing_helper call surfaces as unresolved evidence, not a match."""
    workspace, index = gold_workspace
    payload, _ = _orient(workspace, index, ("broken",))
    handles = _handles_by_name(payload)

    result = inspect_symbols(
        index, InspectRequest(symbols=(handles["broken"],)), workspace_root=workspace
    )
    inspected = json.loads(result)
    entry = inspected["symbols"][0]
    unresolved_groups = [
        group for group in entry.get("callees", []) if group.get("n") == "missing_helper"
    ]
    assert len(unresolved_groups) == 1
    assert "h" not in unresolved_groups[0]
    site = unresolved_groups[0]["sites"][0]
    assert site.get("res") in {"unresolved", "ambiguous", None}


def test_heuristic_receiver_call_keeps_its_stored_resolution(
    gold_workspace: tuple[Path, SymbolIndex],
) -> None:
    """Gate: relay's cache.load call is reported with its stored heuristic class."""
    workspace, index = gold_workspace
    payload, _ = _orient(workspace, index, ("relay",))
    handles = _handles_by_name(payload)

    result = inspect_symbols(
        index, InspectRequest(symbols=(handles["relay"],)), workspace_root=workspace
    )
    inspected = json.loads(result)
    entry = inspected["symbols"][0]
    load_groups = [group for group in entry.get("callees", []) if "load" in str(group.get("n", ""))]
    assert load_groups, "the cache.load call must be visible evidence"
    site = load_groups[0]["sites"][0]
    assert site.get("res") not in {"exact", "scoped"}


def test_two_call_transcript_is_byte_deterministic(
    gold_workspace: tuple[Path, SymbolIndex],
) -> None:
    workspace, index = gold_workspace
    orient_request = OrientRequest(terms=("open_repository", "Repository"))
    first = orient_workspace(index, orient_request, workspace_root=workspace)
    second = orient_workspace(index, orient_request, workspace_root=workspace)
    assert first == second
    handles = _handles_by_name(json.loads(first))
    inspect_request = InspectRequest(symbols=(handles["open_repository"],))
    assert inspect_symbols(index, inspect_request, workspace_root=workspace) == inspect_symbols(
        index, inspect_request, workspace_root=workspace
    )
