"""End-to-end fixture: real parsing, indexing, resolution, seeds, and projection.

Everything here runs through `index_workspace` against a synthetic multi-file
package, so the assertions cover the full pipeline instead of hand-built rows.
Assertions stay architectural (kinds, paths, policy, resolution classes), not tied
to any real repository's filenames.
"""

import json
from pathlib import Path

import pytest

from synapse.core.context import ContextQuery, Direction, query_context
from synapse.core.index import SymbolIndex
from synapse.core.indexing import index_workspace
from synapse.core.models import RelationKind, ResolutionMethod
from synapse.core.workspace import db_path

_FILES = {
    "app/__init__.py": ("from app.store import Repository\nfrom app.factory import open_repository\n"),
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
        "    return open_repository()\n"
    ),
    "main.py": ("from app import open_repository\n\ndef main():\n    return open_repository()\n"),
    "tests/test_service.py": (
        "from app.service import handle_request\n\n"
        "def test_handle_request_works():\n"
        "    assert handle_request(1)\n\n"
        "def test_handle_request_twice():\n"
        "    assert handle_request(2)\n"
    ),
}


@pytest.fixture
def real_workspace(tmp_path: Path) -> tuple[Path, SymbolIndex]:
    workspace = tmp_path / "workspace"
    for relative_path, source in _FILES.items():
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    index_workspace(workspace)
    return workspace, SymbolIndex(db_path(workspace))


def test_decorated_functions_and_factory_calls_index_end_to_end(
    real_workspace: tuple[Path, SymbolIndex],
) -> None:
    _, index = real_workspace
    decorated = index.get_definition("handle_request")
    assert [str(symbol.kind) for symbol in decorated] == ["function"]

    with index.read_session() as reads:
        relations = reads.relations_from_symbols(
            [symbol.id for symbol in index.get_definition("sync_records")],
            kinds=(RelationKind.REFERENCES,),
        )
    save = next(relation for relation in relations if relation.to_name == "save")
    assert save.resolution is ResolutionMethod.EXACT
    assert "Repository.save" in (save.to_symbol_id or "")


def test_exact_production_seed_suppresses_test_variants_end_to_end(
    real_workspace: tuple[Path, SymbolIndex],
) -> None:
    workspace, index = real_workspace
    result = query_context(
        index,
        ContextQuery(question="Trace `handle_request` end to end."),
        workspace_root=workspace,
    )
    payload = json.loads(result)
    assert [seed["name"] for seed in payload["seeds"]] == ["handle_request"]
    assert payload["seeds"][0]["file"] == "app/service.py"
    alternate_names = {seed["name"] for seed in payload.get("alternates", {}).get("items", [])}
    assert "test_handle_request_works" in alternate_names
    assert payload["coverage"]["projection"]["policy"] == "production-focus"


def test_identifier_free_question_orients_on_public_production_surfaces(
    real_workspace: tuple[Path, SymbolIndex],
) -> None:
    workspace, index = real_workspace
    result = query_context(
        index,
        ContextQuery(question="Объясни архитектуру и ключевые точки входа."),
        workspace_root=workspace,
    )
    payload = json.loads(result)
    assert payload["coverage"]["seeds"]["origin"] == "structural-fallback"
    seed_files = [seed["file"] for seed in payload["seeds"]]
    seed_names = [seed["name"] for seed in payload["seeds"]]
    assert all(not file.startswith("tests/") for file in seed_files)
    assert all(not name.startswith("test_") for name in seed_names)
    # The imported public surface (store/factory) must outrank test callers and
    # incidental helpers: the top seed lives in an imported production module.
    assert seed_files[0] in {"app/store.py", "app/factory.py", "app/service.py"}
    tests_coverage = payload["coverage"]["projection"]["tests"]
    assert tests_coverage["discovered"] >= tests_coverage["projected"]
    node_files = [node["file"] for node in payload.get("nodes", [])]
    production_nodes = [file for file in node_files if not file.startswith("tests/")]
    test_nodes = [file for file in node_files if file.startswith("tests/")]
    assert len(production_nodes) >= len(test_nodes)


def test_incoming_test_callers_stay_visible_but_bounded(
    real_workspace: tuple[Path, SymbolIndex],
) -> None:
    workspace, index = real_workspace
    result = query_context(
        index,
        ContextQuery(question="who calls `handle_request`?", direction=Direction.IN),
        workspace_root=workspace,
    )
    payload = json.loads(result)
    test_nodes = [node for node in payload.get("nodes", []) if node["file"].startswith("tests/")]
    assert 1 <= len(test_nodes) <= 5
    assert all(node["via"]["res"] in {"exact", "scoped", "unique-name"} for node in test_nodes)
