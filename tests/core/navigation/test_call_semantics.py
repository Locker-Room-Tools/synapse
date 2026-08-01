"""Callers and callees are call-proven, never inferred from a declaration kind.

Every scenario runs through the real pipeline (parse, index, resolve) because the
property under test is exactly what the extractor stores in `usage_kind`. The rule:
`callers`/`callees` hold only sites whose syntax proves control transfers into the
target; everything else is neutral `refs_in`/`refs_out` with its usage kind verbatim.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from synapse.core.index import SymbolIndex, symbol_handle
from synapse.core.indexing import index_workspace
from synapse.core.navigation import InspectRequest, inspect_symbols
from synapse.core.workspace import db_path


def _indexed(tmp_path: Path, files: dict[str, str]) -> tuple[Path, SymbolIndex]:
    workspace = tmp_path / "workspace"
    for relative_path, source in files.items():
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    index_workspace(workspace)
    return workspace, SymbolIndex(db_path(workspace))


def _inspect(workspace: Path, index: SymbolIndex, name: str) -> dict[str, Any]:
    with index.read_session() as reads:
        candidates = [s for s in reads.get_definition(name) if s.name == name]
    assert candidates, f"{name} is not indexed"
    handle = symbol_handle(candidates[0].id)
    result = inspect_symbols(
        index,
        InspectRequest(symbols=(handle,), token_budget=4000),
        workspace_root=workspace,
    )
    payload = json.loads(result)
    entry = payload["symbols"][0]
    assert isinstance(entry, dict)
    entry["_coverage"] = payload["coverage"]
    return entry


def _uses(entry: dict[str, Any], key: str) -> set[str]:
    """Every stored usage kind projected under one relation key."""
    groups = entry.get(key) or []
    assert isinstance(groups, list)
    return {
        str(site["use"])
        for group in groups
        for site in group["sites"]
        if isinstance(site, dict) and "use" in site
    }


def _names(entry: dict[str, Any], key: str) -> set[str]:
    groups = entry.get(key) or []
    assert isinstance(groups, list)
    return {str(group["n"]) for group in groups if "n" in group}


_CSHARP = {
    "Repo.cs": (
        "namespace App;\npublic class Repo\n{\n"
        "    public int Total { get; set; }\n"
        "    public void Save(int x) { }\n"
        "}\n"
    ),
    "Marker.cs": "namespace App;\npublic class Marker { }\n",
    "Base.cs": "namespace App;\npublic class Base { }\n",
    "Consumer.cs": (
        "namespace App;\npublic class Consumer : Base\n{\n"
        # Declared type, invocation through a receiver, construction, member read,
        # return type, type literal, and a cast — one of each position.
        "    public Repo Build(Repo incoming)\n"
        "    {\n"
        "        var made = new Repo();\n"
        "        made.Save(1);\n"
        "        var total = made.Total;\n"
        "        var t = typeof(Marker);\n"
        "        var cast = (Marker)null;\n"
        "        return made;\n"
        "    }\n"
        "}\n"
    ),
}


@pytest.fixture
def csharp_workspace(tmp_path: Path) -> tuple[Path, SymbolIndex]:
    return _indexed(tmp_path, _CSHARP)


def test_csharp_receiver_invocation_is_a_call(
    csharp_workspace: tuple[Path, SymbolIndex],
) -> None:
    """`made.Save(1)` proves a call, so Save has Build as a caller."""
    workspace, index = csharp_workspace
    entry = _inspect(workspace, index, "Save")

    assert "Build" in _names(entry, "callers")
    assert _uses(entry, "callers") == {"invocation"}


def test_csharp_object_creation_is_a_call(
    csharp_workspace: tuple[Path, SymbolIndex],
) -> None:
    """Documented policy: `new Repo()` transfers control into a constructor."""
    workspace, index = csharp_workspace
    entry = _inspect(workspace, index, "Repo")

    assert "object-creation" in _uses(entry, "callers")


def test_csharp_type_positions_are_never_calls(
    csharp_workspace: tuple[Path, SymbolIndex],
) -> None:
    """A declared type, return type, or member read is not a call at any endpoint kind."""
    workspace, index = csharp_workspace
    entry = _inspect(workspace, index, "Repo")

    # `Repo incoming` and `public Repo Build` are declaration syntax, not calls.
    neutral = _uses(entry, "refs_in")
    assert {"declared-type", "return-type"} <= neutral
    assert not neutral & {"invocation"}


def test_csharp_base_type_attribute_and_type_literal_are_never_calls(
    csharp_workspace: tuple[Path, SymbolIndex],
) -> None:
    """A base list and a `typeof`/cast target stay neutral evidence."""
    workspace, index = csharp_workspace

    base_entry = _inspect(workspace, index, "Base")
    assert _uses(base_entry, "refs_in") == {"base-type"}
    assert not base_entry.get("callers")

    marker_entry = _inspect(workspace, index, "Marker")
    assert _uses(marker_entry, "refs_in") <= {"type-literal", "cast-and-pattern"}
    assert not marker_entry.get("callers")


def test_csharp_member_read_is_not_a_call(
    csharp_workspace: tuple[Path, SymbolIndex],
) -> None:
    """`made.Total` shares the member-access pattern with a call but is only a read."""
    workspace, index = csharp_workspace
    entry = _inspect(workspace, index, "Total")

    assert _uses(entry, "refs_in") == {"member-access"}
    assert not entry.get("callers")


_PYTHON = {
    "app/store.py": (
        "class Base:\n    pass\n\n\n"
        "class Repository(Base):\n"
        "    def save(self, record):\n        return record\n"
    ),
    "app/service.py": (
        "import functools\n"
        "from app.store import Repository\n\n\n"
        "def helper():\n    return 1\n\n\n"
        "@functools.cache\n"
        "def run():\n"
        "    helper()\n"
        "    return Repository().save(1)\n\n\n"
        "def broken():\n"
        "    return missing_helper()\n"
    ),
}


@pytest.fixture
def python_workspace(tmp_path: Path) -> tuple[Path, SymbolIndex]:
    return _indexed(tmp_path, _PYTHON)


def test_python_plain_call_is_a_call(python_workspace: tuple[Path, SymbolIndex]) -> None:
    """Python stored usage_kind=None before; a plain call must now prove itself."""
    workspace, index = python_workspace
    entry = _inspect(workspace, index, "helper")

    assert "run" in _names(entry, "callers")
    assert _uses(entry, "callers") == {"invocation"}


def test_python_superclass_is_not_promoted_to_a_call(
    python_workspace: tuple[Path, SymbolIndex],
) -> None:
    """`class Repository(Base)` is a declaration position, not an invocation."""
    workspace, index = python_workspace
    entry = _inspect(workspace, index, "Base")

    assert _uses(entry, "refs_in") == {"base-type"}
    assert not entry.get("callers")


def test_python_decorator_is_not_promoted_to_a_call(
    python_workspace: tuple[Path, SymbolIndex],
) -> None:
    """A bare decorator name is captured and labelled, but never as a call."""
    workspace, index = python_workspace
    entry = _inspect(workspace, index, "run")

    assert "decorator" not in _uses(entry, "callees")
    assert "decorator" not in _uses(entry, "callers")


def test_unresolved_call_keeps_its_name_and_kind_without_inventing_a_target(
    python_workspace: tuple[Path, SymbolIndex],
) -> None:
    """An unresolvable call is still a call; it just has no handle to point at."""
    workspace, index = python_workspace
    entry = _inspect(workspace, index, "broken")

    groups = entry["callees"]
    assert isinstance(groups, list)
    unresolved = [group for group in groups if group.get("n") == "missing_helper"]
    assert len(unresolved) == 1
    assert "h" not in unresolved[0], "an unresolved target must not be given a handle"
    site = unresolved[0]["sites"][0]
    assert site["use"] == "invocation"
    assert site["res"] == "unresolved"


def test_a_mixed_endpoint_splits_instead_of_upgrading_its_non_call_sites(
    tmp_path: Path,
) -> None:
    """One endpoint that both calls and declares yields two homogeneous groups."""
    workspace, index = _indexed(
        tmp_path,
        {
            "Repo.cs": "namespace App;\npublic class Repo\n{\n    public void Save() { }\n}\n",
            "Mixed.cs": (
                "namespace App;\npublic class Mixed\n{\n"
                # `Repo repo` is a declared type; `new Repo()` is a call. Same endpoint,
                # same enclosing declaration, two different verdicts.
                "    public void Use(Repo repo)\n"
                "    {\n"
                "        var made = new Repo();\n"
                "    }\n"
                "}\n"
            ),
        },
    )
    entry = _inspect(workspace, index, "Repo")

    assert _uses(entry, "callers") == {"object-creation"}
    assert _uses(entry, "refs_in") == {"declared-type"}
    # The same endpoint appears on both sides — split, not merged and upgraded.
    assert "Use" in _names(entry, "callers")
    assert "Use" in _names(entry, "refs_in")


def test_stored_resolution_and_confidence_survive_the_call_split(
    python_workspace: tuple[Path, SymbolIndex],
) -> None:
    """Classifying a site as a call never rewrites how strongly it was resolved."""
    workspace, index = python_workspace
    entry = _inspect(workspace, index, "helper")

    sites = [site for group in entry["callers"] for site in group["sites"]]
    assert sites
    for site in sites:
        assert site["res"] in {"exact", "scoped", "unique-name", "ambiguous", "unresolved"}
        assert site["conf"] in {"high", "medium", "low"}
