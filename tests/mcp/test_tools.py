"""Tests for MCP tool delegation."""

import json
from pathlib import Path

import pytest

from synapse.core.config import (
    active_ignore_matcher,
    write_global_ignored_directories,
    write_project_ignored_directories,
)
from synapse.core.index import SymbolIndex, symbol_handle
from synapse.core.indexing import IndexStats
from synapse.core.lifecycle import EnsureWorkspaceResult, WorkspaceNotReadyError
from synapse.core.models import Confidence, Relation, RelationKind, Symbol, SymbolKind
from synapse.core.navigation import (
    INSPECT_DEFAULT_TOKEN_BUDGET,
    ORIENT_DEFAULT_TOKEN_BUDGET,
    InspectRequest,
    OrientRequest,
)
from synapse.core.provenance import runtime_provenance
from synapse.mcp import tools
from synapse.mcp.workspace import configure_workspace


def _symbol(name: str, symbol_id: str) -> Symbol:
    return Symbol(
        id=symbol_id,
        language="python",
        kind=SymbolKind.FUNCTION,
        native_kind="function_definition",
        name=name,
        qualified_name=name,
        file_path="sample.py",
        container_id=None,
        start_line=1,
        end_line=2,
        start_byte=0,
        end_byte=10,
        signature=f"def {name}():",
        source="tree-sitter",
        confidence=Confidence.HIGH,
    )


class _FakeIndex:
    def search_symbols_page(
        self,
        query: str,
        **_: object,
    ) -> tuple[list[Symbol], dict[str, object]]:
        return [_symbol(query, "sym-1")], _page(returned=1, total=1, limit=20)

    def get_symbol(self, symbol_id: str) -> Symbol | None:
        return _symbol("helper", symbol_id)

    def get_definition(self, name: str) -> list[Symbol]:
        if name == "ambiguous":
            return [_symbol("helper", "sym-1"), _symbol("helper", "sym-2")]
        return [_symbol(name, "sym-1")]

    def get_definition_page(
        self,
        name: str,
        **_: object,
    ) -> tuple[list[Symbol], dict[str, object]]:
        candidates = self.get_definition(name)
        return candidates, _page(returned=len(candidates), total=len(candidates))

    def get_file_outline(self, file_path: str, **_: object) -> dict[str, object] | None:
        return {
            "file_path": file_path,
            "language": "python",
            "symbols": [],
            "returned": 0,
            "total": 0,
            "truncated": False,
        }

    def workspace_stats(self) -> dict[str, object]:
        return {"files": 1, "symbols": 1, "languages": []}

    def project_map(self, **_: object) -> dict[str, object]:
        return {
            "tree": {"sample.py": None},
            "top_symbols": [],
            "page": _page(returned=1, total=1),
        }

    def get_file_dependencies(self, file_path: str, **_: object) -> dict[str, object] | None:
        return {
            "file_path": file_path,
            "imports": ["os"],
            "page": _page(returned=1, total=1),
        }

    def get_symbol_context(
        self,
        symbol_id: str,
        include_body: bool = False,
        **_: object,
    ) -> dict[str, object]:
        return {
            "symbol": {"symbol_id": symbol_id},
            "parent": None,
            "children": [],
            "body": None,
            "page": _page(returned=0, total=0),
        }

    def get_dependencies(self, symbol_id: str) -> list[Relation]:
        return [
            Relation(
                id=f"contains:{symbol_id}:sym-2",
                kind=RelationKind.CONTAINS,
                from_symbol_id=symbol_id,
                to_symbol_id="sym-2",
                from_file_path="sample.py",
                to_file_path="sample.py",
                to_name="method",
                source="tree-sitter",
                confidence=Confidence.HIGH,
            )
        ]

    def get_dependencies_page(
        self,
        symbol_id: str,
        **_: object,
    ) -> tuple[list[Relation], dict[str, object]]:
        relations = self.get_dependencies(symbol_id)
        return relations, _page(returned=len(relations), total=len(relations))

    def find_references(
        self,
        *,
        symbol_id: str | None = None,
        name: str | None = None,
        **_: object,
    ) -> dict[str, object]:
        return {
            "items": [],
            "files": [symbol_id or name or ""],
            "page": _page(returned=0, total=0),
        }

    def related_symbols(
        self,
        symbol_id: str,
        limit: int = 20,
        **_: object,
    ) -> dict[str, object]:
        return {
            "symbol": {"symbol_id": symbol_id},
            "related": [{"limit": limit}],
            "page": _page(returned=1, total=1, limit=limit),
        }

    def compact_context(self, symbol_id: str) -> dict[str, object]:
        return {"symbol": {"symbol_id": symbol_id}, "file": "sample.py"}


def _page(
    *,
    returned: int,
    total: int,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    return {
        "limit": limit,
        "offset": offset,
        "returned": returned,
        "total": total,
        "has_more": offset + returned < total,
    }


def test_synapse_index_workspace_returns_serializable_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The indexing tool returns the dataclass payload shape."""
    monkeypatch.setattr(tools, "require_workspace_ready", lambda path: path)
    monkeypatch.setattr(
        tools,
        "index_workspace",
        lambda path=".", *, force=False: IndexStats(str(path), 1, 2, 3, 0, 4, 5, ["python"]),
    )

    result = tools.synapse_index_workspace(".", force=True)

    assert result["indexed_files"] == 1
    assert result["total_symbols"] == 5
    assert result["languages"] == ["python"]


def test_symbol_lookup_tools_delegate_to_the_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search, definition, outline, and context tools stay thin."""
    monkeypatch.setattr(tools, "_workspace_index", lambda path=".": _FakeIndex())
    monkeypatch.setattr(tools, "_workspace_root", lambda path=".": tmp_path)

    assert tools.synapse_search_symbols("helper") == {
        "items": [
            {
                "symbol_id": "sym-1",
                "handle": symbol_handle("sym-1"),
                "language": "python",
                "kind": "function",
                "name": "helper",
                "qualified_name": "helper",
                "file_path": "sample.py",
                "line_range": [1, 2],
                "signature": "def helper():",
                "confidence": "high",
            }
        ],
        "page": _page(returned=1, total=1, limit=20),
    }
    assert tools.synapse_get_definition(symbol_id="sym-1") == {
        "symbol_id": "sym-1",
        "handle": symbol_handle("sym-1"),
        "language": "python",
        "kind": "function",
        "name": "helper",
        "qualified_name": "helper",
        "file_path": "sample.py",
        "line_range": [1, 2],
        "signature": "def helper():",
        "confidence": "high",
    }
    assert tools.synapse_get_definition(name="helper") == {
        "symbol_id": "sym-1",
        "handle": symbol_handle("sym-1"),
        "language": "python",
        "kind": "function",
        "name": "helper",
        "qualified_name": "helper",
        "file_path": "sample.py",
        "line_range": [1, 2],
        "signature": "def helper():",
        "confidence": "high",
    }
    candidates = tools.synapse_get_definition(name="ambiguous")
    assert candidates == {
        "candidates": [
            {
                "symbol_id": "sym-1",
                "handle": symbol_handle("sym-1"),
                "language": "python",
                "kind": "function",
                "name": "helper",
                "qualified_name": "helper",
                "file_path": "sample.py",
                "line_range": [1, 2],
                "signature": "def helper():",
                "confidence": "high",
            },
            {
                "symbol_id": "sym-2",
                "handle": symbol_handle("sym-2"),
                "language": "python",
                "kind": "function",
                "name": "helper",
                "qualified_name": "helper",
                "file_path": "sample.py",
                "line_range": [1, 2],
                "signature": "def helper():",
                "confidence": "high",
            },
        ],
        "page": _page(returned=2, total=2),
    }
    assert tools.synapse_get_file_outline("sample.py") == {
        "file_path": "sample.py",
        "language": "python",
        "symbols": [],
        "returned": 0,
        "total": 0,
        "truncated": False,
    }
    assert tools.synapse_workspace_stats() == {
        "files": 1,
        "symbols": 1,
        "languages": [],
        "runtime": runtime_provenance().to_payload(),
    }
    monkeypatch.setattr(tools, "watch_status_payload", lambda path: {"running": False})
    assert tools.synapse_watch_status() == {"running": False}
    assert tools.synapse_project_map() == {
        "tree": {"sample.py": None},
        "top_symbols": [],
        "page": _page(returned=1, total=1),
    }
    assert tools.synapse_get_file_dependencies(str(tmp_path / "sample.py")) == {
        "file_path": "sample.py",
        "imports": ["os"],
        "page": _page(returned=1, total=1),
    }
    assert tools.synapse_get_symbol_context("sym-1") == {
        "symbol": {"symbol_id": "sym-1"},
        "parent": None,
        "children": [],
        "body": None,
        "page": _page(returned=0, total=0),
    }
    assert tools.synapse_get_dependencies("sym-1") == {
        "items": [
            {
                "kind": "contains",
                "from_symbol_id": "sym-1",
                "to_symbol_id": "sym-2",
                "to_name": "method",
                "from_file_path": "sample.py",
                "to_file_path": "sample.py",
                "line": None,
                "byte_column": None,
                "source": "tree-sitter",
                "confidence": "high",
            }
        ],
        "page": _page(returned=1, total=1),
    }
    assert tools.synapse_find_references(symbol_id="sym-1") == {
        "items": [],
        "files": ["sym-1"],
        "page": _page(returned=0, total=0),
    }
    assert tools.synapse_related_symbols("sym-1", limit=3) == {
        "symbol": {"symbol_id": "sym-1"},
        "related": [{"limit": 3}],
        "page": _page(returned=1, total=1, limit=3),
    }
    assert tools.synapse_compact_context("sym-1") == {
        "symbol": {"symbol_id": "sym-1"},
        "file": "sample.py",
    }


def test_synapse_get_definition_requires_symbol_id_or_name() -> None:
    """The definition tool rejects empty requests."""
    with pytest.raises(ValueError):
        tools.synapse_get_definition()


def test_synapse_find_references_requires_symbol_id_or_name() -> None:
    """The references tool rejects empty requests."""
    with pytest.raises(ValueError):
        tools.synapse_find_references()


def test_entry_tool_docstrings_nudge_synapse_before_raw_search() -> None:
    """Agent-facing tool descriptions should steer broad navigation to Synapse."""
    assert "prefer over grep/ripgrep" in (tools.synapse_search_symbols.__doc__ or "")
    assert "prefer over opening files" in (tools.synapse_get_definition.__doc__ or "")
    assert "prefer before reading a whole file" in (tools.synapse_get_file_outline.__doc__ or "")
    assert "prefer over grep" in (tools.synapse_find_references.__doc__ or "")
    assert "prefer over reading source" in (tools.synapse_compact_context.__doc__ or "")


def test_tool_docstrings_document_contracts() -> None:
    """Docstrings carry the parameter rules and disambiguations agents rely on."""
    assert "symbol_id OR exact name" in (tools.synapse_get_definition.__doc__ or "")
    assert "OR name" in (tools.synapse_find_references.__doc__ or "")
    assert "incoming" in (tools.synapse_find_references.__doc__ or "")
    assert "outgoing" in (tools.synapse_get_dependencies.__doc__ or "")
    assert "recovery" in (tools.synapse_index_workspace.__doc__ or "")
    assert "workspace-relative" in (tools.synapse_get_file_outline.__doc__ or "")
    assert "workspace-relative" in (tools.synapse_get_file_dependencies.__doc__ or "")
    assert "diagnosis only" in (tools.synapse_watch_status.__doc__ or "")
    assert "include_body=True" in (tools.synapse_get_symbol_context.__doc__ or "")
    assert "{found: false" in (tools.synapse_get_symbol_context.__doc__ or "")
    orient_doc = " ".join((tools.synapse_orient.__doc__ or "").split())
    assert "never proof of absence" in orient_doc
    assert "4-8 discriminative" in orient_doc
    assert "up to 12" in orient_doc
    assert "not a natural-language question" in orient_doc
    assert "bounded server-side" in orient_doc
    inspect_doc = " ".join((tools.synapse_inspect.__doc__ or "").split())
    assert "1-8" in inspect_doc
    assert "normally 2-3" in inspect_doc
    assert "relation handles" in inspect_doc
    assert "exact|scoped|unique-name|ambiguous|unresolved" in inspect_doc
    assert "bounded server-side" in inspect_doc
    assert "not proof" in inspect_doc


def test_workspace_root_resolves_relative_paths_from_configured_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP relative workspace paths are resolved from the configured workspace."""
    monkeypatch.setattr("synapse.mcp.workspace._workspace_root", None)
    configure_workspace(tmp_path)
    (tmp_path / "nested").mkdir()

    assert tools._workspace_root(".") == tmp_path
    assert tools._workspace_root("nested") == tmp_path / "nested"


def test_two_workspaces_are_indexed_and_queried_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each workspace keeps its own index even when used from one process."""
    data_root = tmp_path / "data"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))

    workspace_a = tmp_path / "workspace_a"
    workspace_b = tmp_path / "workspace_b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    (workspace_a / "a.py").write_text("def func_a(): pass\n", encoding="utf-8")
    (workspace_b / "b.py").write_text("def func_b(): pass\n", encoding="utf-8")
    monkeypatch.setattr(tools, "require_workspace_ready", lambda path: path)

    stats_a = tools.synapse_index_workspace(str(workspace_a))
    stats_b = tools.synapse_index_workspace(str(workspace_b))

    assert stats_a["indexed_files"] == 1
    assert stats_b["indexed_files"] == 1

    results_a = tools.synapse_search_symbols("func_a", workspace_path=str(workspace_a))
    results_b = tools.synapse_search_symbols("func_b", workspace_path=str(workspace_b))

    items_a = results_a["items"]
    items_b = results_b["items"]
    assert isinstance(items_a, list)
    assert isinstance(items_b, list)
    assert [item["name"] for item in items_a] == ["func_a"]
    assert [item["name"] for item in items_b] == ["func_b"]

    missing_a = tools.synapse_search_symbols("func_b", workspace_path=str(workspace_a))
    missing_b = tools.synapse_search_symbols("func_a", workspace_path=str(workspace_b))

    assert missing_a["items"] == []
    assert missing_b["items"] == []


def test_querying_a_missing_workspace_does_not_create_cache_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP query validation precedes index path allocation."""
    data_root = tmp_path / "data-root"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))

    with pytest.raises(NotADirectoryError, match="Workspace is not a directory"):
        tools.synapse_search_symbols("helper", workspace_path=str(tmp_path / "missing"))

    assert not data_root.exists()


def test_synapse_ensure_workspace_returns_lifecycle_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP bootstrap tool exposes the shared initialized/reused/repaired contract."""
    expected = EnsureWorkspaceResult(
        workspace_path=str(tmp_path),
        action="repaired",
        initialized=True,
        daemon={"running": True, "degraded": False, "backend": "polling", "pid": 123},
        index={"files": 3, "symbols": 7, "languages": ["python"]},
        runtime=runtime_provenance().to_payload(),
    )
    monkeypatch.setattr(tools, "ensure_workspace", lambda path: expected)

    result = tools.synapse_ensure_workspace(str(tmp_path))

    assert result == {
        "workspace_path": str(tmp_path),
        "action": "repaired",
        "initialized": True,
        "daemon": {
            "running": True,
            "degraded": False,
            "backend": "polling",
            "pid": 123,
        },
        "index": {"files": 3, "symbols": 7, "languages": ["python"]},
        "runtime": runtime_provenance().to_payload(),
        # Set only when first-run init wrote a .synapseignore into the repository.
        "ignore_bootstrap": None,
    }


@pytest.mark.parametrize(
    ("call", "args"),
    [
        ("synapse_search_symbols", ("helper",)),
        ("synapse_get_definition", (None, "helper")),
        ("synapse_project_map", ()),
    ],
)
def test_query_tools_require_lazy_workspace_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    call: str,
    args: tuple[object, ...],
) -> None:
    """Code queries direct the agent to ensure instead of creating an empty index."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data-root"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))
    tool = getattr(tools, call)

    with pytest.raises(
        WorkspaceNotReadyError,
        match=r"uninitialized.*synapse_ensure_workspace",
    ):
        tool(*args, workspace_path=str(workspace))

    assert not data_root.exists()


def test_manual_index_tool_is_blocked_before_workspace_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure and status are the only MCP operations allowed before readiness."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data-root"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))

    with pytest.raises(
        WorkspaceNotReadyError,
        match="synapse_ensure_workspace",
    ):
        tools.synapse_index_workspace(str(workspace))

    assert not data_root.exists()


def test_watch_status_is_available_before_initialization_and_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap diagnostics report identity without allocating cache state."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data-root"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))

    result = tools.synapse_watch_status(str(workspace))

    assert result["workspace_path"] == str(workspace)
    assert result["initialized"] is False
    assert result["running"] is False
    assert not data_root.exists()


def test_synapse_get_config_describes_the_option_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The config tool is self-describing on a workspace that has no project config yet."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    result = tools.synapse_get_config(str(tmp_path))

    options = result["options"]
    assert isinstance(options, dict)
    option = options["ignore_rules"]
    assert result["project_config_exists"] is False
    assert result["project_config_path"] == str(tmp_path / ".synapse" / "config.json")
    assert option["writes_to"] == str(tmp_path / ".synapseignore")
    assert option["project_source"] == "none"
    assert option["layers"] == ["built-in", "global", "project"]
    assert option["case_sensitive"] is True
    assert option["always_ignored"] == [".git"]
    assert option["add_with"] == "synapse_add_ignored_directories"
    assert "synapse_index_workspace" in option["takes_effect"]
    assert not (tmp_path / ".synapse").exists()

    # Rules are ordered and carry provenance; there is deliberately no flat effective set.
    assert "effective" not in option
    assert {"pattern": ".git/", "scope": "built-in", "negated": False}.items() <= next(
        rule for rule in option["rules"] if rule["pattern"] == ".git/"
    ).items()
    assert all(rule["directory_only"] for rule in option["rules"])
    assert option["rules_total"] == len(option["rules"])
    assert option["rules_complete"] is True
    assert option["skipped_lines"] == []
    assert option["shadowed_project_json"] == []
    assert "no flat effective set exists" in option["coverage"]


def test_synapse_get_config_reports_skipped_lines_and_shadowed_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad line and a superseded legacy config are both surfaced, never silently dropped."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    write_project_ignored_directories(tmp_path, {"legacy"})
    (tmp_path / ".synapseignore").write_text("dist/\n[unterminated\n", encoding="utf-8")

    with pytest.warns(UserWarning, match="supersedes ignored_directories"):
        result = tools.synapse_get_config(str(tmp_path))

    options = result["options"]
    assert isinstance(options, dict)
    option = options["ignore_rules"]
    assert option["project_source"] == "ignore-file"
    assert option["shadowed_project_json"] == ["legacy"]
    assert [line["line"] for line in option["skipped_lines"]] == [2]
    assert option["skipped_lines"][0]["text"] == "[unterminated"


def test_synapse_add_ignored_directories_writes_the_ignore_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding creates .synapseignore and reports normalization back to the agent."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    result = tools.synapse_add_ignored_directories([" *.min.js ", "dist/"], str(tmp_path))

    text = (tmp_path / ".synapseignore").read_text(encoding="utf-8")
    assert result["added"] == ["*.min.js", "dist/"]
    assert result["already_present"] == []
    assert result["created"] is True
    assert result["normalized"] == {" *.min.js ": "*.min.js"}
    assert result["scope"] == "project"
    assert result["config_path"] == str(tmp_path / ".synapseignore")
    assert text.endswith("*.min.js\ndist/\n")

    follow_up = tools.synapse_get_config(str(tmp_path))
    follow_up_options = follow_up["options"]
    assert isinstance(follow_up_options, dict)
    option = follow_up_options["ignore_rules"]
    assert option["project_source"] == "ignore-file"
    assert [rule["pattern"] for rule in option["rules"] if rule["scope"] == "project"] == [
        "*.min.js",
        "dist/",
    ]


def test_synapse_add_ignored_directories_migrates_legacy_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Creating the ignore file moves legacy entries in, so a workspace never has two sources."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    write_project_ignored_directories(tmp_path, {"legacy"})

    result = tools.synapse_add_ignored_directories(["*.min.js"], str(tmp_path))

    payload = json.loads((tmp_path / ".synapse" / "config.json").read_text(encoding="utf-8"))
    assert result["migrated_from_json"] == ["legacy"]
    assert "legacy/" in (tmp_path / ".synapseignore").read_text(encoding="utf-8")
    assert "ignored_directories" not in payload


def test_synapse_add_ignored_directories_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-adding an entry reports it as present and leaves the file byte-identical."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    tools.synapse_add_ignored_directories(["generated/"], str(tmp_path))
    ignore_path = tmp_path / ".synapseignore"
    before = ignore_path.read_bytes()

    result = tools.synapse_add_ignored_directories(["generated/", "generated/"], str(tmp_path))

    assert result["added"] == []
    assert result["already_present"] == ["generated/"]
    assert ignore_path.read_bytes() == before


def test_synapse_remove_ignored_directories_clears_project_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing drops project entries and reports unknown ones without failing."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    tools.synapse_add_ignored_directories(["generated/"], str(tmp_path))

    result = tools.synapse_remove_ignored_directories(["generated/", "vendor/"], str(tmp_path))

    assert result["removed"] == ["generated/"]
    assert result["not_present"] == ["vendor/"]
    assert result["negated"] == []
    assert result["project_rules"] == []


def test_synapse_remove_ignored_directories_negates_a_built_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A built-in cannot be deleted, so removing it appends a negation instead of failing."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    result = tools.synapse_remove_ignored_directories(["node_modules/"], str(tmp_path))

    assert result["negated"] == ["!node_modules/"]
    assert result["removed"] == []
    assert not active_ignore_matcher(tmp_path).ignores_child((), "node_modules")


def test_synapse_remove_ignored_directories_negates_a_global_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An entry inherited from the global config is negated locally, not an error."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    write_global_ignored_directories({"generated"})

    result = tools.synapse_remove_ignored_directories(["generated/"], str(tmp_path))

    assert result["negated"] == ["!generated/"]
    assert not active_ignore_matcher(tmp_path).ignores_child(("pkg",), "generated")


def test_synapse_remove_ignored_directories_cannot_reinclude_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'.git' stays ignored even after a negation is written for it."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    tools.synapse_remove_ignored_directories([".git/"], str(tmp_path))

    assert active_ignore_matcher(tmp_path).ignores_child((), ".git")


@pytest.mark.parametrize("directories", [[], ["../escape"], ["[unterminated"], ["/"]])
def test_config_mutations_reject_invalid_input_without_writing(
    directories: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected call leaves no ignore file behind."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    with pytest.raises(ValueError):
        tools.synapse_add_ignored_directories(directories, str(tmp_path))

    assert not (tmp_path / ".synapseignore").exists()


def test_config_mutations_are_all_or_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One bad entry rejects the whole call, leaving valid siblings unwritten."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    tools.synapse_add_ignored_directories(["generated/"], str(tmp_path))
    ignore_path = tmp_path / ".synapseignore"
    before = ignore_path.read_bytes()

    with pytest.raises(ValueError, match="Invalid ignored directory"):
        tools.synapse_add_ignored_directories(["keep_me/", "../escape"], str(tmp_path))

    assert ignore_path.read_bytes() == before


def test_config_tool_docstrings_document_contracts() -> None:
    """The config tools carry their whole contract in the agent-facing docstring."""
    assert "Self-describing" in (tools.synapse_get_config.__doc__ or "")
    assert "last matching rule" in (tools.synapse_get_config.__doc__ or "")
    assert "gitignore pattern" in (tools.synapse_add_ignored_directories.__doc__ or "")
    assert "next watch sweep" in (tools.synapse_add_ignored_directories.__doc__ or "")
    assert "negated" in (tools.synapse_remove_ignored_directories.__doc__ or "")
    assert "synapse_index_workspace" in (tools.synapse_remove_ignored_directories.__doc__ or "")


def test_synapse_orient_delegates_to_core(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The orientation tool stays a thin delegator returning the core string."""
    captured: dict[str, object] = {}

    def fake_orient(index: SymbolIndex, request: OrientRequest, *, workspace_root: Path) -> str:
        captured["request"] = request
        captured["workspace_root"] = workspace_root
        return '{"matches":[]}'

    monkeypatch.setattr(tools, "_navigation_workspace", lambda path=".": tmp_path)
    monkeypatch.setattr(tools, "orient_workspace", fake_orient)

    result = tools.synapse_orient(
        terms=["open_repository", "app/store.py"],
        path_scope="app",
    )

    assert result == '{"matches":[]}'
    assert captured["workspace_root"] == tmp_path
    request = captured["request"]
    assert isinstance(request, OrientRequest)
    assert request.terms == ("open_repository", "app/store.py")
    assert request.path_scope == "app"
    assert request.token_budget == ORIENT_DEFAULT_TOKEN_BUDGET


def test_synapse_orient_accepts_empty_terms_for_map_orientation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_orient(index: SymbolIndex, request: OrientRequest, *, workspace_root: Path) -> str:
        captured["request"] = request
        return "{}"

    monkeypatch.setattr(tools, "_navigation_workspace", lambda path=".": tmp_path)
    monkeypatch.setattr(tools, "orient_workspace", fake_orient)

    tools.synapse_orient()
    request = captured["request"]
    assert isinstance(request, OrientRequest)
    assert request.terms == ()


def test_synapse_inspect_delegates_to_core(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inspection tool stays a thin delegator returning the core string."""
    captured: dict[str, object] = {}

    def fake_inspect(index: SymbolIndex, request: InspectRequest, *, workspace_root: Path) -> str:
        captured["request"] = request
        captured["workspace_root"] = workspace_root
        return '{"symbols":[]}'

    monkeypatch.setattr(tools, "_navigation_workspace", lambda path=".": tmp_path)
    monkeypatch.setattr(tools, "inspect_symbols", fake_inspect)

    result = tools.synapse_inspect(["s_" + "A" * 22, "py:stable-id"])

    assert result == '{"symbols":[]}'
    assert captured["workspace_root"] == tmp_path
    request = captured["request"]
    assert isinstance(request, InspectRequest)
    assert request.symbols == ("s_" + "A" * 22, "py:stable-id")
    assert request.token_budget == INSPECT_DEFAULT_TOKEN_BUDGET


def test_navigation_tools_delegate_readiness_to_core(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness is one core call per navigation tool; MCP keeps no decision of its own."""
    prepared: list[Path] = []

    def fake_ready(path: str | Path) -> Path:
        prepared.append(Path(path))
        return tmp_path

    monkeypatch.setattr(tools, "_workspace_root", lambda path=".": tmp_path)
    monkeypatch.setattr(tools, "ensure_navigation_ready", fake_ready)
    monkeypatch.setattr(tools, "orient_workspace", lambda index, request, *, workspace_root: "{}")
    monkeypatch.setattr(tools, "inspect_symbols", lambda index, request, *, workspace_root: "{}")

    tools.synapse_orient(terms=["anything"])
    tools.synapse_inspect(symbols=["s_" + "A" * 22])

    assert prepared == [tmp_path, tmp_path]
    # The whole decision (uninitialized, degraded, missing grammars, stale references)
    # lives in core.lifecycle; nothing here may branch on workspace state.
    assert not hasattr(tools, "workspace_status_payload")


class _EmptyIndex:
    """Duck-typed index where nothing is found."""

    def get_symbol(self, symbol_id: str) -> Symbol | None:
        return None

    def get_definition(self, name: str) -> list[Symbol]:
        return []

    def get_definition_page(
        self,
        name: str,
        **_: object,
    ) -> tuple[list[Symbol], dict[str, object]]:
        return [], _page(returned=0, total=0)

    def get_file_outline(self, file_path: str, **_: object) -> dict[str, object] | None:
        return None

    def get_file_dependencies(self, file_path: str, **_: object) -> dict[str, object] | None:
        return None

    def get_symbol_context(self, symbol_id: str, **_: object) -> dict[str, object] | None:
        return None

    def related_symbols(self, symbol_id: str, **_: object) -> dict[str, object] | None:
        return None

    def compact_context(self, symbol_id: str) -> dict[str, object] | None:
        return None


def test_not_found_is_a_uniform_envelope_never_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every optional-result tool returns the same {found: false} envelope."""
    monkeypatch.setattr(tools, "_workspace_index", lambda path=".": _EmptyIndex())
    monkeypatch.setattr(tools, "_workspace_root", lambda path=".": tmp_path)

    results = {
        "symbol": tools.synapse_get_definition(symbol_id="missing-id"),
        "name": tools.synapse_get_definition(name="missing_name"),
        "outline": tools.synapse_get_file_outline("missing.py"),
        "file_deps": tools.synapse_get_file_dependencies("missing.py"),
        "context": tools.synapse_get_symbol_context("missing-id"),
        "related": tools.synapse_related_symbols("missing-id"),
        "compact": tools.synapse_compact_context("missing-id"),
    }

    for result in results.values():
        assert result["found"] is False
        assert result["reason"] == "not-indexed"
        assert "hint" in result
    assert results["symbol"]["target"] == "missing-id"
    assert results["outline"]["target"] == "missing.py"
