"""Adversarial tests for the hard serialized-size guarantee.

The documented guarantee: the final serialized result never exceeds
``token_budget * CHARS_PER_TOKEN`` characters, and is always valid JSON.
"""

import json
from pathlib import Path

import pytest

from synapse.core.context import ContextQuery, query_context
from synapse.core.context.budget import CHARS_PER_TOKEN, MIN_TOKEN_BUDGET
from synapse.core.index import SymbolIndex
from synapse.core.indexing import REFERENCE_FINGERPRINT_KEY, reference_extraction_fingerprint
from synapse.core.models import Confidence, SourceFile, Symbol, SymbolKind
from synapse.core.workspace import db_path

_HARD_CAP = MIN_TOKEN_BUDGET * CHARS_PER_TOKEN


def _workspace_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, SymbolIndex]:
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data"))
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    index = SymbolIndex(db_path(workspace_root))
    index.set_meta(REFERENCE_FINGERPRINT_KEY, reference_extraction_fingerprint())
    return workspace_root, index


def _add_symbol(index: SymbolIndex, name: str, file_path: str) -> None:
    index.upsert_file(
        SourceFile(
            id=file_path,
            path=file_path,
            language="python",
            project_root=None,
            content_hash=f"hash-{name}",
            indexed_at="2026-07-30T00:00:00+00:00",
        )
    )
    index.replace_symbols_for_file(
        file_path,
        [
            Symbol(
                id=f"python:{file_path}:function:{name}:1",
                language="python",
                kind=SymbolKind.FUNCTION,
                native_kind="test",
                name=name,
                qualified_name=name,
                file_path=file_path,
                container_id=None,
                start_line=1,
                end_line=2,
                start_byte=0,
                end_byte=10,
                signature=f"def {name}():",
                source="test",
                confidence=Confidence.HIGH,
            )
        ],
        [],
    )


def _assert_bounded(result: str) -> dict[str, object]:
    assert len(result) <= _HARD_CAP
    payload = json.loads(result)
    assert isinstance(payload, dict)
    return payload


def test_oversized_question_is_bounded_and_echo_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _add_symbol(index, "entrypoint", "src/main.py")
    question = "explain " + "x" * 100_000
    result = query_context(
        index,
        ContextQuery(question=question, token_budget=MIN_TOKEN_BUDGET),
        workspace_root=workspace_root,
    )
    payload = _assert_bounded(result)
    query_echo = payload["query"]
    assert isinstance(query_echo, dict)
    assert query_echo["question_truncated"] is True
    assert len(query_echo["question"]) <= 240


def test_many_explicit_symbol_ids_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _add_symbol(index, "entrypoint", "src/main.py")
    ids = tuple(
        f"python:src/very/long/module/path_{i:03d}.py:function:name_{i:03d}:1" for i in range(500)
    )
    result = query_context(
        index,
        ContextQuery(question="", symbol_ids=ids, token_budget=MIN_TOKEN_BUDGET),
        workspace_root=workspace_root,
    )
    payload = _assert_bounded(result)
    query_echo = payload["query"]
    assert isinstance(query_echo, dict)
    assert query_echo["symbol_ids_total"] == 500
    assert len(query_echo["symbol_ids"]) <= 10


def test_very_long_ids_and_paths_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    long_path = "src/" + "deep/" * 200 + "module.py"
    _add_symbol(index, "very_" + "long_" * 100 + "name", long_path)
    result = query_context(
        index,
        ContextQuery(
            question="", symbol_ids=("missing-" + "y" * 5_000,), token_budget=MIN_TOKEN_BUDGET
        ),
        workspace_root=workspace_root,
    )
    _assert_bounded(result)


def test_unicode_question_respects_character_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _add_symbol(index, "entrypoint", "src/main.py")
    question = "Объясни архитектуру " * 2_000
    result = query_context(
        index,
        ContextQuery(question=question, token_budget=MIN_TOKEN_BUDGET),
        workspace_root=workspace_root,
    )
    _assert_bounded(result)


def test_json_escaping_content_stays_valid_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    _add_symbol(index, "entrypoint", "src/main.py")
    question = 'quote " backslash \\ newline \n tab \t ' * 300
    result = query_context(
        index,
        ContextQuery(question=question, token_budget=MIN_TOKEN_BUDGET),
        workspace_root=workspace_root,
    )
    _assert_bounded(result)


def test_smallest_budget_with_wide_graph_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root, index = _workspace_index(tmp_path, monkeypatch)
    for i in range(50):
        _add_symbol(index, f"handler_{i:02d}", f"src/pkg_{i % 7}/mod_{i:02d}.py")
    result = query_context(
        index,
        ContextQuery(question="how does it all work", token_budget=1),
        workspace_root=workspace_root,
    )
    _assert_bounded(result)
