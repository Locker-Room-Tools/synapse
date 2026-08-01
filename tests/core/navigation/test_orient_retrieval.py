"""Orientation retrieves names and paths through separate channels.

The failure mode being pinned: name search also matched `file_path`, so path-only rows
filled the bounded page and hid real name matches, and a matched file with no
declarations produced no match object at all — only an unreferenced `files` row.
"""

import json
from pathlib import Path
from typing import Any

from synapse.core.index import SymbolIndex
from synapse.core.indexing import index_workspace
from synapse.core.models import SymbolKind
from synapse.core.navigation import OrientRequest, estimate_tokens, orient_workspace
from synapse.core.workspace import db_path
from tests.core.navigation.builders import add_file, build_index, make_symbol


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return workspace


def _orient(
    index: SymbolIndex,
    workspace: Path,
    terms: tuple[str, ...],
    *,
    token_budget: int = 800,
    path_scope: str | None = None,
) -> dict[str, Any]:
    payload = json.loads(
        orient_workspace(
            index,
            OrientRequest(terms=terms, path_scope=path_scope, token_budget=token_budget),
            workspace_root=workspace,
        )
    )
    assert isinstance(payload, dict)
    return payload


def _indexed(tmp_path: Path, files: dict[str, str]) -> tuple[Path, SymbolIndex]:
    workspace = tmp_path / "workspace"
    for relative_path, source in files.items():
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    index_workspace(workspace)
    return workspace, SymbolIndex(db_path(workspace))


def test_path_only_matches_cannot_crowd_out_a_name_substring_match(tmp_path: Path) -> None:
    """A big file named after the term must not consume the whole bounded page."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    # 40 declarations whose names have nothing to do with "reads", in a file whose
    # path does. A combined name-and-path search returns these first and the real
    # name match never fits the page.
    add_file(
        index,
        "app/reads.py",
        [
            make_symbol(f"py:noise-{i:02d}", f"unrelated_{i:02d}", "app/reads.py", line=i + 1)
            for i in range(40)
        ],
    )
    add_file(
        index,
        "app/stream.py",
        [make_symbol("py:target", "stream_reads_buffer", "app/stream.py", line=1)],
    )

    payload = _orient(index, workspace, ("reads",))
    names = {str(entry["n"]) for entry in payload["matches"]}

    assert "stream_reads_buffer" in names
    assert not any(name.startswith("unrelated_") for name in names)


def test_matched_file_without_declarations_is_an_explicit_file_match(
    tmp_path: Path,
) -> None:
    """The case that used to vanish: a term matches a file that declares nothing."""
    workspace, index = _indexed(
        tmp_path,
        {
            "app/settings.py": "# configuration notes only, no declarations\n",
            "app/service.py": "def run():\n    return 1\n",
        },
    )

    payload = _orient(index, workspace, ("settings.py",))
    file_matches = payload["file_matches"]
    assert isinstance(file_matches, list)

    entries = {payload["files"][entry["f"]]: entry for entry in file_matches}
    assert "app/settings.py" in entries
    assert entries["app/settings.py"]["d"] == 0
    assert entries["app/settings.py"]["t"] == "settings.py"


def test_a_path_only_term_never_returns_an_unexplained_empty_match_list(
    tmp_path: Path,
) -> None:
    """`matches: []` with no gap explanation is what made a path hit look like nothing."""
    workspace, index = _indexed(tmp_path, {"app/notes.py": "# notes only, nothing declared\n"})

    payload = _orient(index, workspace, ("notes.py",))

    # No declaration to rank, but the term did match something and the payload says so:
    # it is neither an empty answer nor an unmatched term.
    assert payload["matches"] == []
    assert "notes.py" not in payload.get("unmatched_terms", [])
    assert payload["file_matches"]
    assert payload["files"] == ["app/notes.py"]


def test_production_files_rank_before_test_and_generated_path_twins(
    tmp_path: Path,
) -> None:
    """A production file named for the term outranks its test twin."""
    workspace, index = _indexed(
        tmp_path,
        {
            "app/service.py": "def run():\n    return 1\n",
            "tests/service.py": "def test_run():\n    assert True\n",
        },
    )

    payload = _orient(index, workspace, ("service.py",))
    ordered = [payload["files"][entry["f"]] for entry in payload["file_matches"]]

    assert ordered.index("app/service.py") < ordered.index("tests/service.py")


def test_every_files_entry_is_referenced_by_a_payload_entry(tmp_path: Path) -> None:
    """A file-table row nothing points at is dead weight in a byte-budgeted payload."""
    workspace, index = _indexed(
        tmp_path,
        {
            "app/service.py": "def run():\n    return 1\n",
            "app/empty.py": "# nothing here\n",
        },
    )

    payload = _orient(index, workspace, ("run", "empty.py", "service.py"))
    referenced: set[int] = set()
    for key in ("matches", "weak", "file_matches"):
        for entry in payload.get(key) or []:
            referenced.add(int(entry["f"]))

    assert referenced == set(range(len(payload["files"])))


def test_fixed_caps_and_name_omissions_are_reported(tmp_path: Path) -> None:
    """The bounds that shaped the answer are visible, not implied."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(
        index,
        "app/big.py",
        [
            make_symbol(f"py:handler-{i:02d}", f"handler_{i:02d}", "app/big.py", line=i + 1)
            for i in range(40)
        ],
    )

    payload = _orient(index, workspace, ("handler",))
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)

    assert coverage["caps"]["names"] == 25
    assert coverage["caps"]["paths"] == 20
    # 40 declarations match the name, the page returns 25, and the gap is stated.
    assert coverage["name_omitted"] == 15


def test_file_match_cap_reports_its_omission(tmp_path: Path) -> None:
    """More matching files than the cap allows is an omission, not a silent trim."""
    workspace, index = _indexed(
        tmp_path,
        {f"pkg{i:02d}/module.py": f"def fn_{i:02d}():\n    return {i}\n" for i in range(12)},
    )

    payload = _orient(index, workspace, ("module.py",))
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)

    assert len(payload["file_matches"]) == 8
    assert coverage["files_omitted"] == 4


def test_name_channel_still_finds_declarations_inside_a_matched_file(
    tmp_path: Path,
) -> None:
    """Separating the channels must not lose the path channel's best declaration."""
    workspace, index = _indexed(
        tmp_path, {"app/store.py": "class Repository:\n    def save(self, r):\n        return r\n"}
    )

    payload = _orient(index, workspace, ("store.py",))
    names = {str(entry["n"]) for entry in payload["matches"]}

    assert "Repository" in names
    assert any(str(entry["m"]) == "path" for entry in payload["matches"])


def test_import_symbols_never_become_matches(tmp_path: Path) -> None:
    """Imports are not declarations; the name channel must keep excluding them."""
    workspace, index = _indexed(
        tmp_path,
        {
            "app/store.py": "class Repository:\n    pass\n",
            "app/use.py": "from app.store import Repository\n",
        },
    )

    payload = _orient(index, workspace, ("Repository",))
    for entry in payload["matches"]:
        assert entry["k"] != str(SymbolKind.IMPORT)


def test_map_orientation_projects_bounded_bridges_within_the_default_budget(
    tmp_path: Path,
) -> None:
    """Bridges name the dependency spine using areas present in the same payload."""
    workspace, index = _indexed(
        tmp_path,
        {
            "api/handlers.py": (
                "from core.store import Repository\n\n\n"
                "def handle(r: Repository):\n    return r.load(1)\n"
            ),
            "api/routes.py": (
                "from api.handlers import handle\n\n\ndef route():\n    return handle(None)\n"
            ),
            "core/store.py": (
                "class Repository:\n"
                "    def load(self, key):\n        return key\n\n"
                "    def save(self, rec):\n        return rec\n"
            ),
            "core/model.py": "class Record:\n    pass\n",
            "worker/jobs.py": (
                "from core.store import Repository\n\n\n"
                "def run_job():\n    return Repository().save(1)\n"
            ),
            "main.py": "from api.routes import route\n\n\ndef main():\n    return route()\n",
        },
    )

    wire = orient_workspace(index, OrientRequest(terms=()), workspace_root=workspace)
    payload = json.loads(wire)
    assert estimate_tokens(wire) <= 800
    assert payload["payload_complete"] is True

    areas = [str(area["p"]) for area in payload["map"]["areas"]]
    bridges = payload["map"]["bridges"]
    assert bridges, "repository-map orientation must expose its trusted cross-area links"

    linked = {(areas[bridge["a"]], areas[bridge["b"]]) for bridge in bridges}
    # The structural path an agent needs: the API and worker areas both reach core.
    assert ("api", "core") in linked
    assert ("worker", "core") in linked
    for bridge in bridges:
        # Compact area references only: both endpoints resolve inside this payload.
        assert 0 <= int(bridge["a"]) < len(areas)
        assert 0 <= int(bridge["b"]) < len(areas)


def test_bridge_examples_drop_before_areas_and_entrypoints(tmp_path: Path) -> None:
    """Under pressure the cheapest map evidence goes first and the loss is reported."""
    files = {
        "core/store.py": "class Repository:\n    def load(self, key):\n        return key\n",
    }
    # The repository map partitions into roughly files/8 areas, so this is sized to
    # produce enough areas that the full map outgrows the smallest accepted budget.
    for area in range(12):
        for module in range(8):
            files[f"feature{area:02d}/submodule{module:02d}/handlers.py"] = (
                "from core.store import Repository\n\n\n"
                f"def handle_{area:02d}_{module:02d}(repository: Repository):\n"
                "    return repository.load(1)\n"
            )
    workspace, index = _indexed(tmp_path, files)

    generous = _orient(index, workspace, (), token_budget=1200)
    assert any("x" in bridge for bridge in generous["map"]["bridges"])
    full_tokens = int(generous["budget"]["estimated_tokens"])

    # Walk the budget down until the very first drop fires, whatever budget that is.
    first_drop: dict[str, Any] = {}
    for token_budget in range(full_tokens, 399, -1):
        dropped = _orient(index, workspace, (), token_budget=token_budget)["budget"].get(
            "dropped", {}
        )
        if dropped:
            first_drop = dict(dropped)
            break

    assert first_drop, "the map payload never came under budget pressure"
    # The cheapest map evidence is what goes first — never an area or an entrypoint.
    assert set(first_drop) == {"map-bridge-example"}


def test_scoped_orientation_reaches_an_in_scope_match_behind_a_full_page(
    tmp_path: Path,
) -> None:
    """The reported reproduction: a scoped term must not come back unmatched.

    30 out-of-scope declarations share the term's stem and fill the bounded page. With
    the scope applied only after retrieval, the one in-scope declaration is never
    fetched and orientation reports the term as unmatched.
    """
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(
        index,
        "outside/noise.py",
        [
            make_symbol(f"py:noise-{i:02d}", f"handler_{i:02d}", "outside/noise.py", line=i + 1)
            for i in range(30)
        ],
    )
    add_file(
        index,
        "inside/target.py",
        [make_symbol("py:target", "handler_target", "inside/target.py", line=1)],
    )

    payload = _orient(index, workspace, ("handler",), path_scope="inside")

    assert [str(entry["n"]) for entry in payload["matches"]] == ["handler_target"]
    assert "handler" not in payload.get("unmatched_terms", [])
    assert payload["files"] == ["inside/target.py"]


def test_scoped_crowd_classification_uses_the_scoped_search_space(tmp_path: Path) -> None:
    """A term that is generic workspace-wide can be rare inside the scope."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(
        index,
        "outside/noise.py",
        [
            make_symbol(f"py:noise-{i:02d}", f"handler_{i:02d}", "outside/noise.py", line=i + 1)
            for i in range(30)
        ],
    )
    add_file(
        index,
        "inside/target.py",
        [make_symbol("py:target", "handler_target", "inside/target.py", line=1)],
    )

    unscoped = _orient(index, workspace, ("handler",))
    scoped = _orient(index, workspace, ("handler",), path_scope="inside")

    # Workspace-wide the term is crowded, so only exact hits survive — and there are none.
    assert unscoped["crowded_terms"] == {"handler": 31}
    # Inside the scope it is rare, so the substring match ranks normally.
    assert "crowded_terms" not in scoped
    assert scoped["matches"]


def test_scoped_name_omission_counts_the_scoped_space(tmp_path: Path) -> None:
    """`name_omitted` must describe what the scoped query withheld, not the workspace."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(
        index,
        "outside/noise.py",
        [
            make_symbol(f"py:out-{i:02d}", f"widget_{i:02d}", "outside/noise.py", line=i + 1)
            for i in range(40)
        ],
    )
    add_file(
        index,
        "inside/mod.py",
        [
            make_symbol(f"py:in-{i:02d}", f"widget_in_{i:02d}", "inside/mod.py", line=i + 1)
            for i in range(30)
        ],
    )

    unscoped = _orient(index, workspace, ("widget",))
    scoped = _orient(index, workspace, ("widget",), path_scope="inside")

    # 70 workspace-wide against a 25-row page.
    assert unscoped["coverage"]["name_omitted"] == 45
    # 30 inside the scope against the same page.
    assert scoped["coverage"]["name_omitted"] == 5


def test_scoping_to_tests_still_reaches_the_test_twin(tmp_path: Path) -> None:
    """Production-first ranking must not make explicitly scoped test code unreachable."""
    workspace, index = _indexed(
        tmp_path,
        {
            "app/service.py": "def run_task():\n    return 1\n",
            "tests/test_service.py": "def run_task_twin():\n    return 1\n",
        },
    )

    scoped = _orient(index, workspace, ("run_task",), path_scope="tests")
    files = scoped["files"]

    assert scoped["matches"]
    assert all(str(files[entry["f"]]).startswith("tests/") for entry in scoped["matches"])


def test_path_omission_counts_every_matching_file_not_only_retrieved_ones(
    tmp_path: Path,
) -> None:
    """With more matches than the path limit, `files_omitted` must state the real gap."""
    workspace, index = _indexed(
        tmp_path,
        {f"pkg{i:02d}/module.py": f"def fn_{i:02d}():\n    return {i}\n" for i in range(25)},
    )

    payload = _orient(index, workspace, ("module.py",))
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)

    # 25 matched, 8 returned. Counting only the 20 the path limit retrieved would
    # under-report this by 5.
    assert len(payload["file_matches"]) == 8
    assert coverage["files_omitted"] == 17
    # Retrieval truncation is distinct from the public cap and the budget.
    assert coverage["path_capped"] is True
    assert "module.py" not in payload.get("unmatched_terms", [])


def test_path_omission_is_exact_when_nothing_was_capped(tmp_path: Path) -> None:
    """Below the path limit there is no retrieval truncation to report."""
    workspace, index = _indexed(
        tmp_path,
        {f"pkg{i:02d}/module.py": f"def fn_{i:02d}():\n    return {i}\n" for i in range(12)},
    )

    payload = _orient(index, workspace, ("module.py",))
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)

    assert len(payload["file_matches"]) == 8
    assert coverage["files_omitted"] == 4
    assert "path_capped" not in coverage


def test_file_matches_are_deterministic_under_the_path_limit(tmp_path: Path) -> None:
    """Two orientations over one index state return the same bytes."""
    workspace, index = _indexed(
        tmp_path,
        {f"pkg{i:02d}/module.py": f"def fn_{i:02d}():\n    return {i}\n" for i in range(25)},
    )

    first = orient_workspace(index, OrientRequest(terms=("module.py",)), workspace_root=workspace)
    second = orient_workspace(index, OrientRequest(terms=("module.py",)), workspace_root=workspace)

    assert first == second
    payload = json.loads(first)
    ordered = [payload["files"][entry["f"]] for entry in payload["file_matches"]]
    assert ordered == sorted(ordered)


def test_path_omission_stays_honest_after_budget_degradation(tmp_path: Path) -> None:
    """Dropping file matches for the budget must not shrink the reported shortfall."""
    workspace, index = _indexed(
        tmp_path,
        {f"pkg{i:02d}/module.py": f"def fn_{i:02d}():\n    return {i}\n" for i in range(25)},
    )

    squeezed = _orient(index, workspace, ("module.py",), token_budget=400)
    coverage = squeezed["coverage"]
    assert isinstance(coverage, dict)

    returned = len(squeezed.get("file_matches", []))
    # Whatever the budget kept, matched minus returned still adds up.
    assert coverage.get("files_omitted", 0) == 25 - returned
    assert coverage["path_capped"] is True


def _imports(path: str, names: list[str]) -> list[Any]:
    return [
        make_symbol(f"py:imp-{name}", name, path, kind=SymbolKind.IMPORT, line=i + 1)
        for i, name in enumerate(names)
    ]


def test_imports_cannot_consume_the_page_and_hide_a_scoped_declaration(
    tmp_path: Path,
) -> None:
    """The reported reproduction: 30 imports fill the page and hide the real function."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(
        index,
        "inside/imports.py",
        _imports("inside/imports.py", [f"handler_{i:02d}" for i in range(30)]),
    )
    add_file(
        index,
        "inside/target.py",
        [make_symbol("py:target", "handler_target", "inside/target.py", line=1)],
    )

    payload = _orient(index, workspace, ("handler",), path_scope="inside")

    assert [str(entry["n"]) for entry in payload["matches"]] == ["handler_target"]
    assert "handler" not in payload.get("unmatched_terms", [])
    # 30 imports no longer inflate the crowd count past the floor.
    assert "crowded_terms" not in payload


def test_imports_do_not_hide_a_declaration_without_a_scope(tmp_path: Path) -> None:
    """The same defect exists unscoped; the SQL exclusion covers both."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(
        index, "app/imports.py", _imports("app/imports.py", [f"widget_{i:02d}" for i in range(30)])
    )
    add_file(index, "app/target.py", [make_symbol("py:target", "widget_target", "app/target.py")])

    payload = _orient(index, workspace, ("widget",))

    assert "widget_target" in {str(entry["n"]) for entry in payload["matches"]}
    assert "widget" not in payload.get("unmatched_terms", [])


def test_import_kinds_never_appear_among_orientation_matches(tmp_path: Path) -> None:
    """Imports are not declarations, whatever the ranking would otherwise do."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(
        index,
        "app/use.py",
        [
            *_imports("app/use.py", ["handler"]),
            make_symbol("py:decl", "handler_impl", "app/use.py", line=2),
        ],
    )

    payload = _orient(index, workspace, ("handler",))
    kinds = {str(entry["k"]) for entry in payload["matches"]}

    assert kinds
    assert str(SymbolKind.IMPORT) not in kinds


def test_name_omission_describes_only_the_declaration_search_space(tmp_path: Path) -> None:
    """`name_omitted` counts declarations withheld, never imports that never qualified."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(
        index, "app/imports.py", _imports("app/imports.py", [f"widget_{i:02d}" for i in range(40)])
    )
    add_file(
        index,
        "app/decls.py",
        [
            make_symbol(f"py:decl-{i:02d}", f"widget_decl_{i:02d}", "app/decls.py", line=i + 1)
            for i in range(30)
        ],
    )

    payload = _orient(index, workspace, ("widget",))
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)

    # 30 declarations against a 25-row page. The 40 imports are not part of the space.
    assert coverage["name_omitted"] == 5


def test_a_small_crowded_scope_inside_a_large_workspace_is_crowded(
    tmp_path: Path,
) -> None:
    """Crowding compares the scoped numerator with the scoped population, not the world."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    # 5,000 unrelated declarations outside the scope would raise a global threshold to 50.
    for chunk in range(10):
        path = f"outside/bulk{chunk:02d}.py"
        add_file(
            index,
            path,
            [
                make_symbol(
                    f"py:out-{chunk:02d}-{i:03d}", f"other_{chunk:02d}_{i:03d}", path, line=i + 1
                )
                for i in range(500)
            ],
        )
    add_file(
        index,
        "inside/mod.py",
        [
            make_symbol(f"py:in-{i:02d}", f"handler_{i:02d}", "inside/mod.py", line=i + 1)
            for i in range(30)
        ],
    )

    scoped = _orient(index, workspace, ("handler",), path_scope="inside")

    # 30 matches against a scoped population of 30 exceeds the floor of 25.
    assert scoped["crowded_terms"] == {"handler": 30}


def test_a_globally_common_term_that_is_rare_in_scope_is_not_crowded(
    tmp_path: Path,
) -> None:
    """The inverse: a scope where the term discriminates fine keeps its weak matches."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(
        index,
        "outside/bulk.py",
        [
            make_symbol(f"py:out-{i:02d}", f"handler_{i:02d}", "outside/bulk.py", line=i + 1)
            for i in range(60)
        ],
    )
    add_file(
        index,
        "inside/mod.py",
        [make_symbol("py:in", "handler_target", "inside/mod.py", line=1)],
    )

    unscoped = _orient(index, workspace, ("handler",))
    scoped = _orient(index, workspace, ("handler",), path_scope="inside")

    assert unscoped["crowded_terms"] == {"handler": 61}
    assert "crowded_terms" not in scoped
    assert [str(entry["n"]) for entry in scoped["matches"]] == ["handler_target"]


def test_imports_do_not_raise_the_crowd_threshold(tmp_path: Path) -> None:
    """Imports are excluded from the population as well as the numerator."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    # 5,000 imports would lift a symbol-count threshold to 50 and mask the crowding.
    for chunk in range(10):
        path = f"inside/imports{chunk:02d}.py"
        add_file(
            index,
            path,
            _imports(path, [f"noise_{chunk:02d}_{i:03d}" for i in range(500)]),
        )
    add_file(
        index,
        "inside/mod.py",
        [
            make_symbol(f"py:in-{i:02d}", f"handler_{i:02d}", "inside/mod.py", line=i + 1)
            for i in range(30)
        ],
    )

    payload = _orient(index, workspace, ("handler",), path_scope="inside")

    assert payload["crowded_terms"] == {"handler": 30}


def test_exact_matches_survive_a_crowded_term(tmp_path: Path) -> None:
    """Crowding demotes weak hits; it never discards a term's exact declaration."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(
        index,
        "inside/mod.py",
        [
            *[
                make_symbol(f"py:in-{i:02d}", f"handler_{i:02d}", "inside/mod.py", line=i + 2)
                for i in range(30)
            ],
            make_symbol("py:exact", "handler", "inside/mod.py", line=1),
        ],
    )

    payload = _orient(index, workspace, ("handler",), path_scope="inside")

    assert payload["crowded_terms"] == {"handler": 31}
    assert [str(entry["n"]) for entry in payload["matches"]] == ["handler"]


def test_unscoped_crowding_is_deterministic(tmp_path: Path) -> None:
    """Two orientations over one index state classify identically."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(
        index,
        "app/mod.py",
        [
            make_symbol(f"py:{i:02d}", f"handler_{i:02d}", "app/mod.py", line=i + 1)
            for i in range(30)
        ],
    )

    first = orient_workspace(index, OrientRequest(terms=("handler",)), workspace_root=workspace)
    second = orient_workspace(index, OrientRequest(terms=("handler",)), workspace_root=workspace)

    assert first == second
    assert json.loads(first)["crowded_terms"] == {"handler": 30}


def test_a_bare_term_matching_no_path_component_reports_no_path_omission(
    tmp_path: Path,
) -> None:
    """The reported contradiction: unmatched term *and* 25 omitted files in one payload."""
    workspace, index = _indexed(
        tmp_path,
        {
            f"pkg/handler_noise_{i:02d}.py": f"def unrelated_{i:02d}():\n    return {i}\n"
            for i in range(25)
        },
    )

    payload = _orient(index, workspace, ("handler",))
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)

    assert payload["matches"] == []
    assert payload.get("file_matches") in (None, [])
    assert payload["unmatched_terms"] == ["handler"]
    # A bare word is not a path component of any of those files, so nothing was
    # eligible and nothing can have been omitted.
    assert "files_omitted" not in coverage
    assert "path_capped" not in coverage


def test_a_bare_term_still_matches_a_whole_path_component(tmp_path: Path) -> None:
    """Narrowing the rule must not lose the exact/suffix form orientation relies on."""
    workspace, index = _indexed(
        tmp_path,
        {
            "pkg/handler.py": "def run():\n    return 1\n",
            "pkg/handler_noise.py": "def other():\n    return 2\n",
        },
    )

    payload = _orient(index, workspace, ("handler.py",))
    matched = [payload["files"][entry["f"]] for entry in payload["file_matches"]]

    assert "pkg/handler.py" in matched
    assert "handler.py" not in payload.get("unmatched_terms", [])
