"""Tests for the materialized repository map derivation and storage."""

from pathlib import Path

from synapse.core.index import (
    REPO_MAP_DERIVATION_VERSION,
    SymbolIndex,
    compute_repo_map,
    load_repo_map,
    refresh_repo_map,
)
from synapse.core.index.repo_map import (
    MAX_AREAS,
    REPO_MAP_VERSION_KEY,
    serialize_repo_map,
)
from synapse.core.models import (
    Confidence,
    Relation,
    RelationKind,
    ResolutionMethod,
    SourceFile,
    Symbol,
    SymbolKind,
)


def _symbol(
    symbol_id: str,
    name: str,
    file_path: str,
    *,
    kind: SymbolKind = SymbolKind.FUNCTION,
    line: int = 1,
) -> Symbol:
    return Symbol(
        id=symbol_id,
        language="python",
        kind=kind,
        native_kind="test",
        name=name,
        qualified_name=name,
        file_path=file_path,
        container_id=None,
        start_line=line,
        end_line=line + 1,
        start_byte=line * 10,
        end_byte=line * 10 + 5,
        signature=None,
        source="test",
        confidence=Confidence.HIGH,
    )


def _reference(
    relation_id: str,
    *,
    from_symbol_id: str,
    to_symbol_id: str | None,
    from_file_path: str,
    resolution: ResolutionMethod,
    line: int = 1,
) -> Relation:
    return Relation(
        id=relation_id,
        kind=RelationKind.REFERENCES,
        from_symbol_id=from_symbol_id,
        to_symbol_id=to_symbol_id,
        from_file_path=from_file_path,
        to_file_path=None,
        to_name=None,
        source="test",
        confidence=Confidence.HIGH,
        start_line=line,
        start_byte_col=0,
        resolution=resolution,
        usage_kind="call",
        to_qualified_name=None,
    )


def _import(
    relation_id: str, *, from_symbol_id: str, from_file_path: str, to_name: str
) -> Relation:
    return Relation(
        id=relation_id,
        kind=RelationKind.IMPORTS,
        from_symbol_id=from_symbol_id,
        to_symbol_id=None,
        from_file_path=from_file_path,
        to_file_path=None,
        to_name=to_name,
        source="test",
        confidence=Confidence.HIGH,
        start_line=1,
        start_byte_col=0,
        resolution=None,
        usage_kind=None,
        to_qualified_name=None,
    )


def _add_file(
    index: SymbolIndex,
    path: str,
    symbols: list[Symbol],
    relations: tuple[Relation, ...] | list[Relation] = (),
) -> None:
    index.upsert_file(
        SourceFile(
            id=path,
            path=path,
            language="python",
            project_root="/workspace",
            content_hash=f"hash-{path}",
            indexed_at="2026-07-30T00:00:00+00:00",
        )
    )
    index.replace_symbols_for_file(path, symbols, relations)


def test_area_partition_is_bounded_and_covers_every_file(tmp_path: Path) -> None:
    index = SymbolIndex(tmp_path / "index.sqlite")
    total_files = 0
    for module in range(20):
        for item in range(2):
            path = f"src/module{module:02d}/file{item}.py"
            _add_file(index, path, [_symbol(f"s:{path}", f"fn_{module}_{item}", path)])
            total_files += 1
    with index.read_session() as reads:
        repo_map = compute_repo_map(reads)
    assert 1 <= len(repo_map.areas) <= MAX_AREAS
    assert sum(area.files for area in repo_map.areas) == total_files
    assert sum(area.symbols for area in repo_map.areas) == total_files


def test_chain_collapse_produces_deep_area_paths(tmp_path: Path) -> None:
    index = SymbolIndex(tmp_path / "index.sqlite")
    for area in ("core", "mcp"):
        for item in range(3):
            path = f"src/synapse/{area}/file{item}.py"
            _add_file(index, path, [_symbol(f"s:{path}", f"fn_{area}_{item}", path)])
    with index.read_session() as reads:
        repo_map = compute_repo_map(reads)
    assert [area.path for area in repo_map.areas] == ["src/synapse/core", "src/synapse/mcp"]


def test_test_directories_are_flagged(tmp_path: Path) -> None:
    index = SymbolIndex(tmp_path / "index.sqlite")
    for item in range(3):
        _add_file(
            index,
            f"src/app/file{item}.py",
            [_symbol(f"s:src{item}", f"fn{item}", f"src/app/file{item}.py")],
        )
        _add_file(
            index,
            f"tests/test_file{item}.py",
            [_symbol(f"s:test{item}", f"test_fn{item}", f"tests/test_file{item}.py")],
        )
    with index.read_session() as reads:
        repo_map = compute_repo_map(reads)
    flags = {area.path: area.is_tests for area in repo_map.areas}
    assert flags == {"src/app": False, "tests": True}


def test_anchors_use_only_trusted_references(tmp_path: Path) -> None:
    index = SymbolIndex(tmp_path / "index.sqlite")
    trusted = _symbol("s:core/trusted", "TrustedService", "core/service.py", kind=SymbolKind.CLASS)
    popular = _symbol("s:core/popular", "popular_helper", "core/helpers.py")
    _add_file(index, "core/service.py", [trusted])
    _add_file(index, "core/helpers.py", [popular])
    callers = []
    relations = []
    for item in range(3):
        caller = _symbol(f"s:app/caller{item}", f"caller{item}", "app/callers.py", line=item + 1)
        callers.append(caller)
        relations.append(
            _reference(
                f"ref:exact:{item}",
                from_symbol_id=caller.id,
                to_symbol_id=trusted.id,
                from_file_path="app/callers.py",
                resolution=ResolutionMethod.EXACT,
                line=item + 1,
            )
        )
    for item in range(5):
        relations.append(
            _reference(
                f"ref:heuristic:{item}",
                from_symbol_id=callers[0].id,
                to_symbol_id=popular.id,
                from_file_path="app/callers.py",
                resolution=ResolutionMethod.UNIQUE_NAME,
                line=10 + item,
            )
        )
    _add_file(index, "app/callers.py", callers, relations)
    with index.read_session() as reads:
        repo_map = compute_repo_map(reads)
    core_area = next(area for area in repo_map.areas if area.path == "core")
    anchor_names = [anchor.name for anchor in core_area.anchors]
    assert anchor_names[0] == "TrustedService"
    assert core_area.anchors[0].trusted_in == 3
    heuristic_anchor = [anchor for anchor in core_area.anchors if anchor.name == "popular_helper"]
    assert all(anchor.trusted_in == 0 for anchor in heuristic_anchor)


def test_bridges_require_exact_or_scoped_cross_area_references(tmp_path: Path) -> None:
    index = SymbolIndex(tmp_path / "index.sqlite")
    store = _symbol("s:core/store", "Store", "core/store.py", kind=SymbolKind.CLASS)
    other = _symbol("s:lib/other", "Other", "lib/other.py", kind=SymbolKind.CLASS)
    _add_file(index, "core/store.py", [store])
    _add_file(index, "lib/other.py", [other])
    app_symbol = _symbol("s:app/main", "main", "app/main.py")
    _add_file(
        index,
        "app/main.py",
        [app_symbol],
        [
            _reference(
                "ref:app-core",
                from_symbol_id=app_symbol.id,
                to_symbol_id=store.id,
                from_file_path="app/main.py",
                resolution=ResolutionMethod.EXACT,
                line=5,
            ),
            _reference(
                "ref:app-lib",
                from_symbol_id=app_symbol.id,
                to_symbol_id=other.id,
                from_file_path="app/main.py",
                resolution=ResolutionMethod.UNIQUE_NAME,
                line=6,
            ),
        ],
    )
    with index.read_session() as reads:
        repo_map = compute_repo_map(reads)
    pairs = {(bridge.from_area, bridge.to_area): bridge for bridge in repo_map.bridges}
    assert ("app", "core") in pairs
    assert pairs[("app", "core")].references == 1
    assert pairs[("app", "core")].examples == ("app/main.py:5",)
    assert all(pair != ("app", "lib") or bridge.references == 0 for pair, bridge in pairs.items())


def test_import_segment_bridge_matching(tmp_path: Path) -> None:
    index = SymbolIndex(tmp_path / "index.sqlite")
    store = _symbol("s:core/store", "Store", "core/store.py", kind=SymbolKind.CLASS)
    _add_file(index, "core/store.py", [store])
    import_symbol = _symbol("s:app/import", "core.store", "app/main.py", kind=SymbolKind.IMPORT)
    _add_file(
        index,
        "app/main.py",
        [import_symbol],
        [
            _import(
                "imp:app-core",
                from_symbol_id=import_symbol.id,
                from_file_path="app/main.py",
                to_name="core.store",
            )
        ],
    )
    with index.read_session() as reads:
        repo_map = compute_repo_map(reads)
    pairs = {(bridge.from_area, bridge.to_area): bridge for bridge in repo_map.bridges}
    assert pairs[("app", "core")].imports == 1
    assert pairs[("app", "core")].references == 0


def test_entrypoints_use_generic_conventions_only(tmp_path: Path) -> None:
    index = SymbolIndex(tmp_path / "index.sqlite")
    main_fn = _symbol("s:app/main-fn", "main", "app/entry.py")
    _add_file(index, "app/entry.py", [main_fn])
    cli_class = _symbol("s:tool/cli", "ToolRunner", "tool/cli.py", kind=SymbolKind.CLASS)
    _add_file(index, "tool/cli.py", [cli_class])
    ordinary = _symbol("s:tool/util", "helper", "tool/util.py")
    _add_file(index, "tool/util.py", [ordinary])
    test_main = _symbol("s:tests/main", "main", "tests/test_entry.py")
    _add_file(index, "tests/test_entry.py", [test_main])
    with index.read_session() as reads:
        repo_map = compute_repo_map(reads)
    by_signal = {(entry.name, entry.signal) for entry in repo_map.entrypoints}
    assert ("main", "name-convention") in by_signal
    assert ("ToolRunner", "file-convention") in by_signal
    entry_ids = {entry.symbol_id for entry in repo_map.entrypoints}
    assert ordinary.id not in entry_ids
    assert test_main.id not in entry_ids


def test_map_bytes_identical_for_reordered_inserts(tmp_path: Path) -> None:
    files = [
        ("core/store.py", "Store", SymbolKind.CLASS),
        ("app/main.py", "main", SymbolKind.FUNCTION),
        ("lib/util.py", "helper", SymbolKind.FUNCTION),
    ]
    serialized: list[str] = []
    for ordering in (files, list(reversed(files))):
        index = SymbolIndex(tmp_path / f"index-{len(serialized)}.sqlite")
        for path, name, kind in ordering:
            _add_file(index, path, [_symbol(f"s:{path}", name, path, kind=kind)])
        with index.read_session() as reads:
            serialized.append(serialize_repo_map(compute_repo_map(reads)))
    assert serialized[0] == serialized[1]


def test_refresh_and_load_round_trip(tmp_path: Path) -> None:
    index = SymbolIndex(tmp_path / "index.sqlite")
    _add_file(index, "app/main.py", [_symbol("s:app/main", "main", "app/main.py")])
    with index.transaction() as connection:
        refresh_repo_map(connection)
    with index.read_session() as reads:
        stored = load_repo_map(reads)
    assert stored is not None
    assert stored.version == REPO_MAP_DERIVATION_VERSION
    assert [area.path for area in stored.areas] == ["app"]


def test_version_mismatch_loads_none(tmp_path: Path) -> None:
    index = SymbolIndex(tmp_path / "index.sqlite")
    _add_file(index, "app/main.py", [_symbol("s:app/main", "main", "app/main.py")])
    with index.transaction() as connection:
        refresh_repo_map(connection)
    index.set_meta(REPO_MAP_VERSION_KEY, "0")
    with index.read_session() as reads:
        assert load_repo_map(reads) is None


def test_empty_index_yields_empty_map(tmp_path: Path) -> None:
    index = SymbolIndex(tmp_path / "index.sqlite")
    with index.read_session() as reads:
        repo_map = compute_repo_map(reads)
    assert repo_map.areas == ()
    assert repo_map.entrypoints == ()
    assert repo_map.bridges == ()


def test_generated_paths_are_detected() -> None:
    from synapse.core.index import is_generated_path

    assert is_generated_path("App/Data/Migrations/20260724_InitialCreate.cs")
    assert is_generated_path("App/Forms/MainForm.Designer.cs")
    assert is_generated_path("src/__snapshots__/render.test.ts.snap")
    assert is_generated_path("wwwroot/site.min.js")
    assert not is_generated_path("App/Data/DbContext.cs")
    assert not is_generated_path("src/generator.py")


def test_generated_anchors_rank_after_production_anchors(tmp_path: Path) -> None:
    """Migration classes never displace production declarations within an area."""
    index = SymbolIndex(tmp_path / "index.sqlite")
    context = _symbol("s:data/ctx", "AppDbContext", "app/data/context.py", kind=SymbolKind.CLASS)
    migration = _symbol(
        "s:data/mig",
        "InitialCreate",
        "app/data/migrations/m0001.py",
        kind=SymbolKind.CLASS,
    )
    _add_file(index, "app/data/context.py", [context])
    _add_file(index, "app/data/migrations/m0001.py", [migration])
    caller = _symbol("s:data/caller", "open_context", "app/data/factory.py")
    _add_file(
        index,
        "app/data/factory.py",
        [caller],
        [
            _reference(
                "ref:ctx",
                from_symbol_id=caller.id,
                to_symbol_id=context.id,
                from_file_path="app/data/factory.py",
                resolution=ResolutionMethod.EXACT,
                line=2,
            ),
            _reference(
                "ref:mig-1",
                from_symbol_id=caller.id,
                to_symbol_id=migration.id,
                from_file_path="app/data/factory.py",
                resolution=ResolutionMethod.EXACT,
                line=3,
            ),
            _reference(
                "ref:mig-2",
                from_symbol_id=caller.id,
                to_symbol_id=migration.id,
                from_file_path="app/data/factory.py",
                resolution=ResolutionMethod.EXACT,
                line=4,
            ),
        ],
    )
    with index.read_session() as reads:
        repo_map = compute_repo_map(reads)
    all_anchors = [anchor for area in repo_map.areas for anchor in area.anchors]
    names = [anchor.name for anchor in all_anchors]
    # The migration has MORE trusted references, yet production anchors first.
    assert names.index("AppDbContext") < names.index("InitialCreate")


def test_test_bridges_sort_after_production_bridges(tmp_path: Path) -> None:
    index = SymbolIndex(tmp_path / "index.sqlite")
    store = _symbol("s:core/store", "Store", "core/store.py", kind=SymbolKind.CLASS)
    _add_file(index, "core/store.py", [store])
    app_symbol = _symbol("s:app/main", "main", "app/main.py")
    _add_file(
        index,
        "app/main.py",
        [app_symbol],
        [
            _reference(
                "ref:app-core",
                from_symbol_id=app_symbol.id,
                to_symbol_id=store.id,
                from_file_path="app/main.py",
                resolution=ResolutionMethod.EXACT,
                line=5,
            )
        ],
    )
    test_symbols = [
        _symbol(f"s:tests/{item}", f"test_store_{item}", f"tests/test_store_{item}.py")
        for item in range(3)
    ]
    for position, symbol in enumerate(test_symbols):
        _add_file(
            index,
            symbol.file_path,
            [symbol],
            [
                _reference(
                    f"ref:test-{position}",
                    from_symbol_id=symbol.id,
                    to_symbol_id=store.id,
                    from_file_path=symbol.file_path,
                    resolution=ResolutionMethod.EXACT,
                    line=2,
                )
            ],
        )
    with index.read_session() as reads:
        repo_map = compute_repo_map(reads)
    kinds = [
        "test" if bridge.from_area == "tests" or bridge.to_area == "tests" else "production"
        for bridge in repo_map.bridges
    ]
    # Tests carry more raw references, yet the production bridge leads.
    assert kinds[0] == "production"
    assert "test" in kinds


def test_dotted_test_project_directories_are_test_paths() -> None:
    from synapse.core.index import is_test_path

    assert is_test_path("Overlock.Api.Tests/Servers/ValidatorTests.cs")
    assert is_test_path("My.Project.Test/Case.cs")
    assert not is_test_path("Overlock.Api/Servers/Server.cs")
