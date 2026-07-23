"""Tests for MCP tool delegation."""

from pathlib import Path

import pytest

from synapse.core.indexing import IndexStats
from synapse.core.lifecycle import EnsureWorkspaceResult, WorkspaceNotReadyError
from synapse.core.models import Confidence, Relation, RelationKind, Symbol, SymbolKind
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
    assert tools.synapse_workspace_stats() == {"files": 1, "symbols": 1, "languages": []}
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
    assert "synapse_compact_context" in (tools.synapse_get_symbol_context.__doc__ or "")


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

    assert [item["name"] for item in results_a["items"]] == ["func_a"]
    assert [item["name"] for item in results_b["items"]] == ["func_b"]

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
