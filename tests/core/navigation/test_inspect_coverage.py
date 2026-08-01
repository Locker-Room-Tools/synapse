"""Inspection coverage never lets a bounded answer read as a complete one.

The failure mode being pinned: an empty `limitations` list looked like exhaustive
extraction, capped hypotheses were invisible, missing source vanished, and incoming
coverage was described using only the selected symbols' languages even though a caller
can be written in any language in the workspace.
"""

import json
from pathlib import Path
from typing import Any

from synapse.core.index import SymbolIndex, symbol_handle
from synapse.core.indexing import index_workspace
from synapse.core.models import ResolutionMethod
from synapse.core.navigation import InspectRequest, inspect_symbols
from synapse.core.workspace import db_path
from tests.core.navigation.builders import (
    add_file,
    build_index,
    make_reference,
    make_symbol,
)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return workspace


def _inspect(
    index: SymbolIndex,
    workspace: Path,
    symbols: tuple[str, ...],
    *,
    token_budget: int = 4000,
) -> dict[str, Any]:
    payload = json.loads(
        inspect_symbols(
            index,
            InspectRequest(symbols=symbols, token_budget=token_budget),
            workspace_root=workspace,
        )
    )
    assert isinstance(payload, dict)
    return payload


def _extraction(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    entries = coverage.get("extraction") or []
    assert isinstance(entries, list)
    return {str(entry["language"]): entry for entry in entries}


def _indexed(tmp_path: Path, files: dict[str, str]) -> tuple[Path, SymbolIndex]:
    workspace = tmp_path / "workspace"
    for relative_path, source in files.items():
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    index_workspace(workspace)
    return workspace, SymbolIndex(db_path(workspace))


def test_coverage_is_never_advertised_as_exhaustive(tmp_path: Path) -> None:
    """Reference extraction is partial by construction and must say so outright."""
    workspace, index = _indexed(tmp_path, {"app/mod.py": "def target():\n    return 1\n"})
    payload = _inspect(index, workspace, (symbol_handle(_only_id(index, "target")),))

    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    assert coverage["exhaustive"] is False
    assert coverage["resolution_model"] == "syntactic-structural"


def _only_id(index: SymbolIndex, name: str) -> str:
    with index.read_session() as reads:
        matches = [symbol for symbol in reads.get_definition(name) if symbol.name == name]
    assert matches, f"{name} is not indexed"
    return matches[0].id


def test_partial_extraction_without_limitation_ids_still_reports_partial(
    tmp_path: Path,
) -> None:
    """A language with no limitation ids is still partial, not silently complete."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    target = make_symbol("go:target", "Target", "app/main.go")
    add_file(index, "app/main.go", [target])
    # `add_file` records Python; rewrite the row so the file really is Go, a language
    # that advertises no limitations and no usage kinds at all.
    with index.transaction() as connection:
        connection.execute("UPDATE files SET language = 'go' WHERE path = ?", ("app/main.go",))
        connection.execute(
            "UPDATE symbols SET language = 'go' WHERE file_path = ?", ("app/main.go",)
        )

    payload = _inspect(index, workspace, (symbol_handle("go:target"),))
    entry = _extraction(payload)["go"]

    assert entry["limitations"] == []
    assert entry["completeness"] == "partial"
    # The decisive field: no advertised call kinds means no caller/callee evidence
    # exists for this language at all, so an empty `callers` proves nothing.
    assert entry["call_kinds"] == []


def test_more_than_five_hypotheses_report_exact_omission(tmp_path: Path) -> None:
    """Capped hypotheses must carry their true total, not just a truncated list."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    target = make_symbol("py:target", "target", "app/target.py")
    add_file(index, "app/target.py", [target])
    add_file(
        index,
        "app/callers.py",
        [make_symbol("py:caller", "caller", "app/callers.py")],
        [
            make_reference(
                f"r-{i:02d}",
                from_symbol_id="py:caller",
                to_symbol_id=None,
                from_file_path="app/callers.py",
                to_name="target",
                resolution=ResolutionMethod.UNRESOLVED,
                line=i + 1,
            )
            for i in range(8)
        ],
    )

    payload = _inspect(index, workspace, (symbol_handle("py:target"),))
    entry = payload["symbols"][0]
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)

    assert len(entry["hypotheses"]) == 5
    assert entry["hyp_total"] == 8
    assert coverage["hypotheses_total"] == 8
    assert coverage["hypotheses_omitted"] == 3


def test_missing_source_is_visible_rather_than_silently_omitted(tmp_path: Path) -> None:
    """A deleted or stale-located file must not read as a symbol with no body."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    # No file is written to disk, so the slice cannot be read.
    add_file(index, "app/ghost.py", [make_symbol("py:ghost", "ghost", "app/ghost.py")])

    payload = _inspect(index, workspace, (symbol_handle("py:ghost"),))
    entry = payload["symbols"][0]
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)

    assert "src" not in entry
    assert entry["src_unavailable"] is True
    assert coverage["source_unavailable"] == [symbol_handle("py:ghost")]


def test_zero_incoming_in_a_mixed_language_workspace_is_not_proof_of_absence(
    tmp_path: Path,
) -> None:
    """Callers can live in any workspace language, so zero must be read against all."""
    workspace, index = _indexed(
        tmp_path,
        {
            "app/lonely.py": "def lonely():\n    return 1\n",
            "app/Thing.cs": "namespace App;\npublic class Thing { }\n",
        },
    )
    payload = _inspect(index, workspace, (symbol_handle(_only_id(index, "lonely")),))
    entry = payload["symbols"][0]
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    extraction = _extraction(payload)

    assert entry["in_total"] == 0
    assert not entry.get("callers")
    # Every indexed language is calibrated in one structure, so the agent is never
    # pointed at a language it has no call coverage for.
    assert set(extraction) == {"csharp", "python"}
    # Omitted for the evidence-producing language, so the common payload never grows.
    assert "evidence" not in extraction["python"]
    # C# produced none of the returned relations, and says so rather than being named
    # in a second bare list with no metadata attached.
    assert extraction["csharp"]["evidence"] is False
    assert extraction["csharp"]["call_kinds"] == ["invocation", "object-creation"]
    assert extraction["csharp"]["completeness"] == "partial"
    assert coverage["exhaustive"] is False
    assert "workspace_languages" not in coverage


def test_extraction_covers_the_language_of_the_evidence_not_only_the_selection(
    tmp_path: Path,
) -> None:
    """A caller written in another language brings its own extraction limits along."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(index, "app/target.py", [make_symbol("py:target", "target", "app/target.py")])
    add_file(
        index,
        "app/Caller.cs",
        [make_symbol("cs:caller", "Caller", "app/Caller.cs")],
        [
            make_reference(
                "r-cross",
                from_symbol_id="cs:caller",
                to_symbol_id="py:target",
                from_file_path="app/Caller.cs",
                usage_kind="invocation",
            )
        ],
    )
    with index.transaction() as connection:
        connection.execute(
            "UPDATE files SET language = 'csharp' WHERE path = ?", ("app/Caller.cs",)
        )

    payload = _inspect(index, workspace, (symbol_handle("py:target"),))
    extraction = _extraction(payload)

    # The selected symbol is Python, but the evidence was produced by the C# extractor.
    assert {"python", "csharp"} <= set(extraction)
    assert "static-receiver-types" in extraction["csharp"]["limitations"]


def test_budget_shortened_source_is_visible_in_the_entry_and_in_coverage(
    tmp_path: Path,
) -> None:
    """Losing source lines to the budget is a coverage fact, not an invisible trim."""
    workspace = _workspace(tmp_path)
    # Wide enough that the 40-line slice alone exceeds the smallest accepted budget,
    # so the budget — not the fixed line cap — is what removes lines.
    body = "\n".join(f"line {i:02d} " + "x" * 70 for i in range(1, 61)) + "\n"
    (workspace / "app").mkdir(parents=True, exist_ok=True)
    (workspace / "app" / "big.py").write_text(body, encoding="utf-8")
    index = build_index(tmp_path)
    add_file(
        index,
        "app/big.py",
        [make_symbol("py:big", "big", "app/big.py", line=1, end_line=60)],
    )

    generous = _inspect(index, workspace, (symbol_handle("py:big"),), token_budget=4000)
    assert "shortened" not in generous["symbols"][0]["src"]

    squeezed = _inspect(index, workspace, (symbol_handle("py:big"),), token_budget=500)
    entry = squeezed["symbols"][0]
    coverage = squeezed["coverage"]
    assert isinstance(coverage, dict)

    assert entry["src"]["shortened"] is True
    assert coverage["source_shortened"] == [symbol_handle("py:big")]


def test_extraction_language_list_is_bounded_with_explicit_omission(
    tmp_path: Path,
) -> None:
    """Coverage itself is capped, and says so instead of quietly dropping languages."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(index, "app/target.py", [make_symbol("py:target", "target", "app/target.py")])
    languages = ["csharp", "go", "rust", "java", "ruby"]
    for position, language in enumerate(languages):
        path = f"app/caller{position}.ext"
        add_file(
            index,
            path,
            [make_symbol(f"x:caller{position}", f"Caller{position}", path)],
            [
                make_reference(
                    f"r-{position}",
                    from_symbol_id=f"x:caller{position}",
                    to_symbol_id="py:target",
                    from_file_path=path,
                )
            ],
        )
        with index.transaction() as connection:
            connection.execute("UPDATE files SET language = ? WHERE path = ?", (language, path))

    payload = _inspect(index, workspace, (symbol_handle("py:target"),))
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)

    assert len(coverage["extraction"]) == 4
    assert coverage["extraction_omitted"] == 2


def test_payload_complete_stays_about_serialization_only(tmp_path: Path) -> None:
    """A complete payload is never a claim that the evidence or the answer is complete."""
    workspace, index = _indexed(
        tmp_path,
        {
            "app/store.py": "class Repository:\n    def save(self, r):\n        return r\n",
            "app/use.py": (
                "from app.store import Repository\n\n\n"
                "def use():\n    return Repository().save(1)\n"
            ),
        },
    )
    payload = _inspect(index, workspace, (symbol_handle(_only_id(index, "save")),))
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)

    # Everything fit, and the evidence is still explicitly non-exhaustive.
    assert payload["payload_complete"] is True
    assert coverage["exhaustive"] is False
    assert coverage["relations_omitted"] == 0
    assert set(coverage["extraction"][0]) == {
        "language",
        "completeness",
        "call_kinds",
        "limitations",
    }


def _sized_body(lines: int, *, width: int = 70) -> str:
    """A body of exactly `lines` lines, wide enough to matter to the wire budget."""
    return "\n".join(f"line {i:02d} " + "x" * width for i in range(1, lines + 1)) + "\n"


def _with_body(tmp_path: Path, lines: int) -> tuple[Path, SymbolIndex]:
    workspace = _workspace(tmp_path)
    (workspace / "app").mkdir(parents=True, exist_ok=True)
    (workspace / "app" / "body.py").write_text(_sized_body(lines), encoding="utf-8")
    index = build_index(tmp_path)
    add_file(
        index,
        "app/body.py",
        [make_symbol("py:body", "body", "app/body.py", line=1, end_line=lines)],
    )
    return workspace, index


def _source_state(payload: dict[str, Any]) -> set[str]:
    """Which source-cause fields the coverage block reports for this payload."""
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    return {
        key
        for key in (
            "source_truncated",
            "source_shortened",
            "source_omitted",
            "source_unavailable",
        )
        if coverage.get(key)
    }


def test_under_cap_body_shortened_by_budget_is_not_also_called_truncated(
    tmp_path: Path,
) -> None:
    """The reported conflation: a 30-line body is under the fixed cap, so only the
    budget can have removed anything."""
    workspace, index = _with_body(tmp_path, 30)

    payload = _inspect(index, workspace, (symbol_handle("py:body"),), token_budget=500)

    assert _source_state(payload) == {"source_shortened"}
    entry = payload["symbols"][0]
    assert entry["src"]["shortened"] is True
    # Entry-level `truncated` means "the text shown here is incomplete", whatever cause.
    assert entry["src"]["truncated"] is True


def test_over_cap_body_without_budget_pressure_is_only_truncated(tmp_path: Path) -> None:
    """A 60-line body outgrows the fixed 40-line slice with no budget involved."""
    workspace, index = _with_body(tmp_path, 60)

    payload = _inspect(index, workspace, (symbol_handle("py:body"),), token_budget=4000)

    assert _source_state(payload) == {"source_truncated"}
    entry = payload["symbols"][0]
    assert entry["src"]["truncated"] is True
    assert "shortened" not in entry["src"]
    assert len(str(entry["src"]["text"]).splitlines()) == 40


def test_over_cap_body_under_budget_pressure_reports_both_causes(tmp_path: Path) -> None:
    """Two real causes are two entries, not a conflation: the body outgrew the fixed
    slice and the budget then took more."""
    workspace, index = _with_body(tmp_path, 60)

    payload = _inspect(index, workspace, (symbol_handle("py:body"),), token_budget=500)

    assert _source_state(payload) == {"source_truncated", "source_shortened"}
    entry = payload["symbols"][0]
    assert entry["src"]["shortened"] is True
    assert len(str(entry["src"]["text"]).splitlines()) < 40


def test_a_short_body_is_never_reported_as_shortened(tmp_path: Path) -> None:
    """Lowering the bound below the fixed cap loses nothing when the body is shorter."""
    workspace, index = _with_body(tmp_path, 4)

    payload = _inspect(index, workspace, (symbol_handle("py:body"),), token_budget=500)

    assert _source_state(payload) == set()
    entry = payload["symbols"][0]
    assert entry["src"]["truncated"] is False
    assert "shortened" not in entry["src"]


def test_source_dropped_by_the_budget_is_omitted_not_truncated(tmp_path: Path) -> None:
    """When the budget removes the slice entirely, no other cause is claimed."""
    workspace = _workspace(tmp_path)
    (workspace / "app").mkdir(parents=True, exist_ok=True)
    index = build_index(tmp_path)
    # Eight wide bodies at the minimum budget: the drop steps run out of halving room
    # and start removing whole slices.
    handles = []
    for position in range(8):
        name = f"body{position}"
        (workspace / "app" / f"{name}.py").write_text(_sized_body(50), encoding="utf-8")
        add_file(
            index,
            f"app/{name}.py",
            [make_symbol(f"py:{name}", name, f"app/{name}.py", line=1, end_line=50)],
        )
        handles.append(symbol_handle(f"py:{name}"))

    payload = _inspect(index, workspace, tuple(handles), token_budget=500)
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)

    assert coverage.get("source_omitted"), "the budget must report slices it removed"
    omitted = set(coverage["source_omitted"])
    # A dropped slice is not simultaneously reported as truncated or shortened.
    assert not omitted & set(coverage.get("source_truncated", []))
    assert not omitted & set(coverage.get("source_shortened", []))


def test_unreadable_source_is_unavailable_and_nothing_else(tmp_path: Path) -> None:
    """A stale or missing location is its own cause, never a truncation."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(index, "app/ghost.py", [make_symbol("py:ghost", "ghost", "app/ghost.py")])

    payload = _inspect(index, workspace, (symbol_handle("py:ghost"),))

    assert _source_state(payload) == {"source_unavailable"}


def _relabel(index: SymbolIndex, path: str, language: str) -> None:
    """Force a file's indexed language; `add_file` always records Python."""
    with index.transaction() as connection:
        connection.execute("UPDATE files SET language = ? WHERE path = ?", (language, path))
        connection.execute("UPDATE symbols SET language = ? WHERE file_path = ?", (language, path))


def test_zero_incoming_calibrates_a_workspace_language_that_proves_no_calls(
    tmp_path: Path,
) -> None:
    """The decisive case: the other language cannot produce callers at all, and says so."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(index, "app/lonely.py", [make_symbol("py:lonely", "lonely", "app/lonely.py")])
    add_file(index, "svc/main.go", [make_symbol("go:main", "Main", "svc/main.go")])
    _relabel(index, "svc/main.go", "go")

    payload = _inspect(index, workspace, (symbol_handle("py:lonely"),))
    extraction = _extraction(payload)

    assert payload["symbols"][0]["in_total"] == 0
    assert extraction["go"]["evidence"] is False
    # An empty caller set is unreadable without this: Go proves no calls whatsoever,
    # so its usages could never have appeared as callers regardless.
    assert extraction["go"]["call_kinds"] == []


def test_zero_incoming_language_list_stays_bounded_with_exact_omission(
    tmp_path: Path,
) -> None:
    """Calibration is capped like every other list, and reports what it dropped."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(index, "app/lonely.py", [make_symbol("py:lonely", "lonely", "app/lonely.py")])
    for position, language in enumerate(["csharp", "go", "rust", "java", "ruby"]):
        path = f"pkg{position}/mod.ext"
        add_file(index, path, [make_symbol(f"x:{position}", f"Decl{position}", path)])
        _relabel(index, path, language)

    payload = _inspect(index, workspace, (symbol_handle("py:lonely"),))
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    extraction = _extraction(payload)

    assert len(extraction) == 4
    # 6 indexed languages, 4 projected.
    assert coverage["extraction_omitted"] == 2
    # The evidence-producing language is never the one dropped.
    assert "python" in extraction
    assert "evidence" not in extraction["python"]


def test_non_zero_evidence_does_not_pull_in_unrelated_workspace_languages(
    tmp_path: Path,
) -> None:
    """Calibration widens only when a zero needs explaining; otherwise nothing changes."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(index, "app/target.py", [make_symbol("py:target", "target", "app/target.py")])
    add_file(
        index,
        "app/caller.py",
        [make_symbol("py:caller", "caller", "app/caller.py")],
        [
            make_reference(
                "r-in",
                from_symbol_id="py:caller",
                to_symbol_id="py:target",
                from_file_path="app/caller.py",
                usage_kind="invocation",
            )
        ],
    )
    add_file(index, "svc/main.go", [make_symbol("go:main", "Main", "svc/main.go")])
    _relabel(index, "svc/main.go", "go")

    payload = _inspect(index, workspace, (symbol_handle("py:target"),))
    extraction = _extraction(payload)

    assert payload["symbols"][0]["in_total"] == 1
    # Go is indexed but explains nothing here, so it is not projected.
    assert set(extraction) == {"python"}
    assert "evidence" not in extraction["python"]


def test_zero_incoming_calibration_respects_the_wire_budget(tmp_path: Path) -> None:
    """The widened calibration is still bound by the same hard character cap."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(index, "app/lonely.py", [make_symbol("py:lonely", "lonely", "app/lonely.py")])
    for position, language in enumerate(["csharp", "go", "rust"]):
        path = f"pkg{position}/mod.ext"
        add_file(index, path, [make_symbol(f"x:{position}", f"Decl{position}", path)])
        _relabel(index, path, language)

    wire = inspect_symbols(
        index,
        InspectRequest(symbols=(symbol_handle("py:lonely"),), token_budget=500),
        workspace_root=workspace,
    )

    assert len(wire) <= 500 * 4
    assert json.loads(wire)["coverage"]["exhaustive"] is False
