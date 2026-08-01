"""Read projections that keep name retrieval separate from path retrieval.

`search_symbols_page` deliberately matches file paths as well as names, which makes it a
combined channel: on a large file whose path contains the term, a bounded page can be
filled entirely by path-only rows and every real name match becomes unreachable.
"""

from pathlib import Path

import pytest

from synapse.core.index import SymbolIndex
from synapse.core.models import ResolutionMethod, SymbolKind
from tests.core.index.page_assertions import assert_page_is_consistent
from tests.core.navigation.builders import (
    add_file,
    build_index,
    make_reference,
    make_symbol,
)


def test_name_only_search_excludes_file_path_matches(tmp_path: Path) -> None:
    """The name channel returns declarations only, so a path cannot crowd them out."""
    index = build_index(tmp_path)
    add_file(
        index,
        "app/reads.py",
        [
            make_symbol(f"py:noise-{i:02d}", f"unrelated_{i:02d}", "app/reads.py", line=i + 1)
            for i in range(30)
        ],
    )
    add_file(
        index,
        "app/stream.py",
        [make_symbol("py:target", "stream_reads_buffer", "app/stream.py", line=1)],
    )

    with index.read_session() as reads:
        combined, _ = reads.search_symbols_page("reads", limit=10)
        names_only, page = reads.search_symbol_names_page("reads", limit=10)
        crowd_count = reads.symbol_name_match_count("reads")

    # The combined channel keeps its documented behaviour: paths still match.
    assert any(symbol.file_path == "app/reads.py" for symbol in combined)
    assert [symbol.name for symbol in names_only] == ["stream_reads_buffer"]
    # The name-channel total now agrees with the crowd metric instead of counting
    # path rows the caller can never use.
    assert page["total"] == crowd_count == 1


def test_name_only_search_still_matches_qualified_names(tmp_path: Path) -> None:
    """Dropping the path column must not drop qualified-name matching with it."""
    index = build_index(tmp_path)
    add_file(
        index,
        "app/store.py",
        [
            make_symbol(
                "py:save",
                "save",
                "app/store.py",
                qualified_name="Repository.save",
                line=2,
            )
        ],
    )

    with index.read_session() as reads:
        by_qualified, _ = reads.search_symbol_names_page("Repository.save", limit=10)
        by_plain, _ = reads.search_symbol_names_page("save", limit=10)

    assert [symbol.id for symbol in by_qualified] == ["py:save"]
    assert [symbol.id for symbol in by_plain] == ["py:save"]


def test_languages_by_path_reports_indexed_languages_and_omits_unknown_paths(
    tmp_path: Path,
) -> None:
    """Relations carry no language, so evidence coverage reads it from the files table."""
    index = build_index(tmp_path)
    add_file(index, "app/mod.py", [make_symbol("py:one", "one", "app/mod.py")])

    with index.read_session() as reads:
        found = reads.languages_by_path(["app/mod.py", "app/never-indexed.cs"])
        empty = reads.languages_by_path([])

    assert found == {"app/mod.py": "python"}
    assert empty == {}


def test_unresolved_references_by_name_bounds_rows_and_keeps_an_exact_total(
    tmp_path: Path,
) -> None:
    """A common name can carry thousands of sites; the bound must not blur the count."""
    index = build_index(tmp_path)
    add_file(index, "app/target.py", [make_symbol("py:target", "target", "app/target.py")])
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
            for i in range(12)
        ],
    )

    with index.read_session() as reads:
        bounded, total = reads.unresolved_references_by_name("target", limit=5)
        everything, exact = reads.unresolved_references_by_name("target")
        legacy = reads.get_references_by_name("target")

    assert len(bounded) == 5
    assert total == 12
    assert len(everything) == 12
    assert exact == 12
    # The unbounded accessor other callers use is unchanged.
    assert len(legacy) == 12


def test_unresolved_references_total_is_exact_when_the_bound_is_not_reached(
    tmp_path: Path,
) -> None:
    """Under the bound, no extra count query is needed and the total is still right."""
    index = build_index(tmp_path)
    add_file(index, "app/target.py", [make_symbol("py:target", "target", "app/target.py")])
    add_file(
        index,
        "app/callers.py",
        [make_symbol("py:caller", "caller", "app/callers.py")],
        [
            make_reference(
                "r-only",
                from_symbol_id="py:caller",
                to_symbol_id=None,
                from_file_path="app/callers.py",
                to_name="target",
                resolution=ResolutionMethod.UNRESOLVED,
            )
        ],
    )

    with index.read_session() as reads:
        relations, total = reads.unresolved_references_by_name("target", limit=5)

    assert len(relations) == 1
    assert total == 1


def test_name_channel_is_unaffected_by_symbols_in_an_identically_named_file(
    tmp_path: Path,
) -> None:
    """The regression in one line: a file named for the term contributes nothing."""
    index: SymbolIndex = build_index(tmp_path)
    add_file(
        index,
        "app/handler.py",
        [make_symbol("py:unrelated", "process_payload", "app/handler.py")],
    )

    with index.read_session() as reads:
        names_only, page = reads.search_symbol_names_page("handler", limit=10)

    assert names_only == []
    assert page["total"] == 0


def test_scoped_name_search_binds_before_the_page_limit(tmp_path: Path) -> None:
    """Out-of-scope declarations must not consume a bounded page and hide in-scope ones.

    Filtering a global page afterwards is what made a scoped orientation return nothing
    while a valid in-scope match existed.
    """
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

    with index.read_session() as reads:
        unscoped, unscoped_page = reads.search_symbol_names_page("handler", limit=25)
        scoped, scoped_page = reads.search_symbol_names_page(
            "handler", path_scope="inside", limit=25
        )

    # Unscoped, the in-scope declaration is nowhere near the bounded page.
    assert len(unscoped) == 25
    assert "handler_target" not in {symbol.name for symbol in unscoped}
    assert unscoped_page["total"] == 31
    # Scoped, the search space itself is narrowed, so it is the only result.
    assert [symbol.name for symbol in scoped] == ["handler_target"]
    assert scoped_page["total"] == 1


def test_scoped_name_match_count_describes_the_scoped_search_space(tmp_path: Path) -> None:
    """Crowding must be judged against the set the scoped page retrieves from."""
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

    with index.read_session() as reads:
        assert reads.symbol_name_match_count("handler") == 31
        assert reads.symbol_name_match_count("handler", path_scope="inside") == 1
        # The scope itself, not just paths beneath it, is in scope.
        assert reads.symbol_name_match_count("handler", path_scope="inside/target.py") == 1
        assert reads.symbol_name_match_count("handler", path_scope="nowhere") == 0


def test_scoped_search_matches_the_navigation_scope_rule(tmp_path: Path) -> None:
    """A sibling directory sharing the scope's prefix is not inside the scope."""
    index = build_index(tmp_path)
    add_file(index, "app/mod.py", [make_symbol("py:in", "widget", "app/mod.py")])
    add_file(index, "application/mod.py", [make_symbol("py:out", "widget", "application/mod.py")])

    with index.read_session() as reads:
        scoped, page = reads.search_symbol_names_page("widget", path_scope="app", limit=10)

    assert [symbol.id for symbol in scoped] == ["py:in"]
    assert page["total"] == 1


def test_path_channel_reports_its_exact_total_and_respects_scope(tmp_path: Path) -> None:
    """The path limit withholds rows; it must never hide how many matched."""
    index = build_index(tmp_path)
    for area in range(25):
        path = f"pkg{area:02d}/module.py"
        add_file(index, path, [make_symbol(f"py:{area:02d}", f"fn_{area:02d}", path)])
    add_file(index, "inside/module.py", [make_symbol("py:inside", "inside_fn", "inside/module.py")])

    with index.read_session() as reads:
        paths, page = reads.files_matching_path("module.py", limit=20)
        scoped, scoped_page = reads.files_matching_path("module.py", path_scope="inside", limit=20)

    assert len(paths) == 20
    assert page["total"] == 26
    assert page["returned"] == 20
    assert scoped == ["inside/module.py"]
    assert scoped_page["total"] == 1


def test_path_channel_ordering_survives_scoping(tmp_path: Path) -> None:
    """Exact path, then suffix, then substring — unchanged when a scope is applied."""
    index = build_index(tmp_path)
    for path in (
        "app/service.py",
        "app/sub/service.py",
        "app/service_util.py",
        "other/service.py",
    ):
        add_file(index, path, [make_symbol(f"py:{path}", "decl", path)])

    with index.read_session() as reads:
        scoped, _ = reads.files_matching_path("app/service.py", path_scope="app")
        suffix, _ = reads.files_matching_path("service.py", path_scope="app")

    assert scoped[0] == "app/service.py"
    assert suffix == ["app/service.py", "app/sub/service.py"]


def test_distinct_file_count_does_not_double_count_overlapping_terms(tmp_path: Path) -> None:
    """Per-term totals overlap, so only a distinct union gives an exact omission count."""
    index = build_index(tmp_path)
    for path in ("app/service.py", "app/handler.py", "app/other.py"):
        add_file(index, path, [make_symbol(f"py:{path}", "decl", path)])

    with index.read_session() as reads:
        # "service.py" and "app/" both match app/service.py.
        overlapping = reads.count_files_matching_paths(["service.py", "app/"])
        single = reads.count_files_matching_paths(["service.py"])
        scoped = reads.count_files_matching_paths(["service.py"], path_scope="nowhere")
        empty = reads.count_files_matching_paths([])

    assert overlapping == 3
    assert single == 1
    assert scoped == 0
    assert empty == 0


def test_declaration_only_search_excludes_imports_before_the_page_limit(
    tmp_path: Path,
) -> None:
    """Imports must not consume the bounded page and hide a real declaration."""
    index = build_index(tmp_path)
    add_file(
        index,
        "inside/imports.py",
        [
            make_symbol(
                f"py:import-{i:02d}",
                f"handler_{i:02d}",
                "inside/imports.py",
                kind=SymbolKind.IMPORT,
                line=i + 1,
            )
            for i in range(30)
        ],
    )
    add_file(
        index,
        "inside/target.py",
        [make_symbol("py:target", "handler_target", "inside/target.py", line=1)],
    )

    with index.read_session() as reads:
        everything, all_page = reads.search_symbol_names_page("handler", limit=25)
        declarations, decl_page = reads.search_symbol_names_page(
            "handler", declarations_only=True, limit=25
        )

    # Unfiltered, the 30 imports fill the page and the declaration is unreachable.
    assert len(everything) == 25
    assert "handler_target" not in {symbol.name for symbol in everything}
    assert all_page["total"] == 31
    # Declaration-only, it is the whole result.
    assert [symbol.name for symbol in declarations] == ["handler_target"]
    assert decl_page["total"] == 1


def test_declaration_only_count_excludes_imports(tmp_path: Path) -> None:
    """The crowd numerator must describe the same set the page retrieves from."""
    index = build_index(tmp_path)
    add_file(
        index,
        "inside/imports.py",
        [
            make_symbol(
                f"py:import-{i:02d}",
                f"handler_{i:02d}",
                "inside/imports.py",
                kind=SymbolKind.IMPORT,
                line=i + 1,
            )
            for i in range(30)
        ],
    )
    add_file(
        index,
        "inside/target.py",
        [make_symbol("py:target", "handler_target", "inside/target.py", line=1)],
    )

    with index.read_session() as reads:
        assert reads.symbol_name_match_count("handler") == 31
        assert reads.symbol_name_match_count("handler", declarations_only=True) == 1
        assert (
            reads.symbol_name_match_count("handler", path_scope="inside", declarations_only=True)
            == 1
        )


def test_full_profile_search_still_retrieves_imports(tmp_path: Path) -> None:
    """`synapse_search_symbols` is a different contract and must keep its imports."""
    index = build_index(tmp_path)
    add_file(
        index,
        "app/use.py",
        [
            make_symbol("py:imp", "collections", "app/use.py", kind=SymbolKind.IMPORT),
            make_symbol("py:decl", "collections_helper", "app/use.py", line=2),
        ],
    )

    with index.read_session() as reads:
        default_page, _ = reads.search_symbols_page("collections", limit=10)
        explicit, _ = reads.search_symbols_page("collections", kind=SymbolKind.IMPORT, limit=10)

    assert {symbol.name for symbol in default_page} == {"collections", "collections_helper"}
    assert [symbol.id for symbol in explicit] == ["py:imp"]


def test_declaration_count_describes_the_scoped_searchable_population(
    tmp_path: Path,
) -> None:
    """The crowd denominator: declarations only, narrowed to the scope."""
    index = build_index(tmp_path)
    add_file(
        index,
        "outside/bulk.py",
        [
            make_symbol(f"py:out-{i:03d}", f"other_{i:03d}", "outside/bulk.py", line=i + 1)
            for i in range(50)
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
    add_file(
        index,
        "inside/imports.py",
        [make_symbol("py:imp", "handler_import", "inside/imports.py", kind=SymbolKind.IMPORT)],
    )

    with index.read_session() as reads:
        assert reads.declaration_count() == 80
        assert reads.declaration_count(path_scope="inside") == 30
        assert reads.declaration_count(path_scope="nowhere") == 0


def test_path_term_shape_governs_retrieval_and_totals_together(tmp_path: Path) -> None:
    """A bare term that only appears mid-filename matches nothing, and counts nothing."""
    index = build_index(tmp_path)
    for position in range(25):
        path = f"pkg/handler_noise_{position:02d}.py"
        add_file(
            index, path, [make_symbol(f"py:{position:02d}", f"unrelated_{position:02d}", path)]
        )

    with index.read_session() as reads:
        bare, bare_page = reads.files_matching_path("handler", limit=20)
        bare_union = reads.count_files_matching_paths(["handler"])
        # The same stem, path-shaped, does match by substring.
        shaped, shaped_page = reads.files_matching_path("pkg/handler", limit=20)
        shaped_union = reads.count_files_matching_paths(["pkg/handler"])

    # Retrieval, its total, and the distinct union all agree on the narrow set.
    assert bare == []
    assert bare_page["total"] == 0
    assert bare_union == 0
    # And all three agree on the broad set, including the retrieval cap.
    assert len(shaped) == 20
    assert shaped_page["total"] == 25
    assert shaped_union == 25


# FTS5's tokenizer treats `.`, `-`, and `_` alike, so a name spelled with any of them is
# a candidate for a query spelled with another. The query below uses `.`, so these are
# the spellings that tokenize identically while sharing no literal substring with it.
@pytest.mark.parametrize("separator", ["-", "_"])
def test_punctuation_normalized_candidates_never_fill_the_page(
    tmp_path: Path,
    separator: str,
) -> None:
    """The reported defect: 25 tokenizer look-alikes hid the one literal match.

    They also made the page self-contradictory — 25 rows returned against a total of 1,
    because retrieval and counting disagreed about what "matching" means.
    """
    index = build_index(tmp_path)
    decoy = f"foo{separator}bar"
    add_file(
        index,
        "app/noise.py",
        [
            make_symbol(f"py:noise-{i:02d}", f"{decoy}-{i:02d}", "app/noise.py", line=i + 1)
            for i in range(30)
        ],
    )
    add_file(
        index,
        "app/target.py",
        [make_symbol("py:target", "xfoo.bar_target", "app/target.py", line=1)],
    )

    with index.read_session() as reads:
        rows, page = reads.search_symbol_names_page("foo.bar", declarations_only=True, limit=25)

    names = [symbol.name for symbol in rows]
    assert names == ["xfoo.bar_target"]
    assert not [name for name in names if name.startswith(decoy)]
    assert page["total"] == 1
    assert page["returned"] == 1
    assert page["has_more"] is False
    assert_page_is_consistent(page)


def test_every_returned_row_contains_the_query_literally(tmp_path: Path) -> None:
    """FTS only proposes candidates; literal containment decides what is returned."""
    index = build_index(tmp_path)
    add_file(
        index,
        "app/mixed.py",
        [
            make_symbol("py:hyphen", "foo-bar-00", "app/mixed.py", line=1),
            make_symbol("py:under", "foo_bar_01", "app/mixed.py", line=2),
            make_symbol("py:dotted", "foo.bar.02", "app/mixed.py", line=3),
            make_symbol("py:mid", "xfoo.bar_target", "app/mixed.py", line=4),
        ],
    )

    with index.read_session() as reads:
        rows, page = reads.search_symbol_names_page("foo.bar", limit=25)

    names = {symbol.name for symbol in rows}
    assert names == {"foo.bar.02", "xfoo.bar_target"}
    assert all("foo.bar" in name for name in names)
    assert page["total"] == 2
    assert_page_is_consistent(page)


def test_full_search_channel_still_requires_literal_containment(tmp_path: Path) -> None:
    """`search_symbols_page` also matches file paths — literally, never by tokenizer."""
    index = build_index(tmp_path)
    add_file(
        index,
        "app/foo-bar-noise.py",
        [make_symbol("py:decoy", "unrelated", "app/foo-bar-noise.py", line=1)],
    )
    add_file(
        index,
        "app/foo.bar.module.py",
        [make_symbol("py:path", "also_unrelated", "app/foo.bar.module.py", line=1)],
    )
    add_file(
        index,
        "app/named.py",
        [make_symbol("py:named", "xfoo.bar_target", "app/named.py", line=1)],
    )

    with index.read_session() as reads:
        rows, page = reads.search_symbols_page("foo.bar", limit=25)

    # The path match qualifies because the path literally contains the query; the
    # hyphenated decoy does not, in its name or its path.
    assert {symbol.id for symbol in rows} == {"py:path", "py:named"}
    assert page["total"] == 2
    assert_page_is_consistent(page)
