"""Bounded per-file top-level declaration read used by the inspect sibling roster."""

from pathlib import Path

from tests.core.navigation.builders import add_file, build_index, make_contains, make_symbol


def test_top_level_symbols_are_bounded_ordered_and_exactly_counted(tmp_path: Path) -> None:
    index = build_index(tmp_path)
    top_level = [
        make_symbol(f"py:t{i:02d}", f"top_{i:02d}", "app/mod.py", line=10 * i) for i in range(1, 6)
    ]
    nested = make_symbol("py:nested", "inner", "app/mod.py", line=12, container_id="py:t01")
    add_file(
        index,
        "app/mod.py",
        [*top_level, nested],
        [make_contains("py:t01", "py:nested", "app/mod.py")],
    )
    add_file(index, "app/other.py", [make_symbol("py:o1", "other_fn", "app/other.py", line=1)])

    with index.read_session() as reads:
        result = reads.top_level_symbols_by_paths(
            ["app/mod.py", "app/other.py", "app/missing.py"], per_file_limit=3
        )

    symbols, total = result["app/mod.py"]
    assert [s.name for s in symbols] == ["top_01", "top_02", "top_03"]
    assert total == 5, "the count is exact even when the page is bounded"
    assert all(s.container_id is None for s in symbols), "nested symbols never appear"

    other_symbols, other_total = result["app/other.py"]
    assert [s.name for s in other_symbols] == ["other_fn"]
    assert other_total == 1

    missing_symbols, missing_total = result["app/missing.py"]
    assert missing_symbols == []
    assert missing_total == 0
