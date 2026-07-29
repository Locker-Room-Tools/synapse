"""Trust invariants for reference extraction and reporting.

These tests exist to stop the tool overstating what it knows: every advertised usage
kind must be backed by a real capture, coverage metadata must stay honest, counts must
agree with the collections they describe, and locations must mean what they claim.
"""

import time
from pathlib import Path
from typing import cast

import pytest

from synapse.core.index import SymbolIndex
from synapse.core.indexing import index_workspace
from synapse.core.indexing.parser import extract_references, parse_file
from synapse.core.languages import ReferenceExtraction, reference_extraction
from synapse.core.languages import reference_usage_kinds as get_reference_usage_kinds
from synapse.core.workspace import db_path

# (usage kind, source, name that must be captured, name that must NOT be captured).
# The negative case is what stops a pattern from silently over-matching.
CSHARP_USAGE_KIND_SAMPLES: tuple[tuple[str, str, str, str], ...] = (
    (
        "member-access",
        "namespace S;\npublic class C { public void M(R r) { var x = r.Total; } }\n",
        "Total",
        "x",
    ),
    (
        "invocation",
        "namespace S;\npublic class C { public void M() { Helper(); } }\n",
        "Helper",
        "M",
    ),
    (
        "nameof",
        "namespace S;\npublic class C { public string M() { return nameof(Widget); } }\n",
        "Widget",
        "nameof",
    ),
    (
        "object-creation",
        "namespace S;\npublic class C { public void M() { var x = new Gadget(); } }\n",
        "Gadget",
        "x",
    ),
    (
        "generic-type",
        "namespace S;\npublic class C { public void M() { DbSet<Item> s = null; } }\n",
        "DbSet",
        "s",
    ),
    (
        "type-argument",
        "namespace S;\npublic class C { public void M() { DbSet<Item> s = null; } }\n",
        "Item",
        "s",
    ),
    (
        "declared-type",
        "namespace S;\npublic class C { public void M(Repo repo) { } }\n",
        "Repo",
        "repo",
    ),
    (
        "return-type",
        "namespace S;\npublic class C { public Report M() { return null; } }\n",
        "Report",
        "M",
    ),
    (
        "type-literal",
        "namespace S;\npublic class C { public void M() { var t = typeof(Marker); } }\n",
        "Marker",
        "t",
    ),
    (
        "cast-and-pattern",
        "namespace S;\npublic class C { public void M(object o) { var c = (Shape)o; } }\n",
        "Shape",
        "o",
    ),
    (
        "attribute",
        "namespace S;\npublic class C { [Obsolete] public void M() { } }\n",
        "Obsolete",
        "M",
    ),
    (
        "base-type",
        "namespace S;\npublic class C : BaseThing { }\n",
        "BaseThing",
        "C",
    ),
)


@pytest.mark.parametrize(
    ("usage_kind", "source", "expected", "not_expected"),
    CSHARP_USAGE_KIND_SAMPLES,
    ids=[sample[0] for sample in CSHARP_USAGE_KIND_SAMPLES],
)
def test_every_advertised_csharp_usage_kind_has_a_positive_and_negative_case(
    tmp_path: Path,
    usage_kind: str,
    source: str,
    expected: str,
    not_expected: str,
) -> None:
    """Each advertised usage kind captures its target and nothing adjacent to it."""
    file_path = tmp_path / f"{usage_kind.replace('-', '_')}.cs"
    file_path.write_text(source, encoding="utf-8")

    symbols = parse_file(file_path, "csharp", workspace_root=tmp_path)
    references = extract_references(file_path, "csharp", symbols, workspace_root=tmp_path)
    by_name = {reference.name: reference for reference in references}

    assert expected in by_name
    assert by_name[expected].usage_kind == usage_kind
    assert not_expected not in by_name


def test_advertised_usage_kinds_and_produced_usage_kinds_agree() -> None:
    """The sample table covers exactly the usage kinds C# advertises."""
    covered = {sample[0] for sample in CSHARP_USAGE_KIND_SAMPLES}
    assert covered == set(get_reference_usage_kinds("csharp"))


def test_csharp_coverage_is_never_advertised_as_exhaustive() -> None:
    """Partial extraction must never be reported as complete."""
    assert reference_extraction("csharp") is not ReferenceExtraction.BROAD
    assert reference_extraction("csharp") is ReferenceExtraction.PARTIAL


def _indexed(tmp_path: Path, files: dict[str, str]) -> SymbolIndex:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    for name, source in files.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    index_workspace(workspace)
    return SymbolIndex(db_path(workspace))


def test_zero_results_report_no_indexed_matches_never_unused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty answer is about the index, not about the symbol being unused."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    index = _indexed(tmp_path, {"a.cs": "namespace S;\npublic class Lonely { }\n"})

    result = index.find_references(name="NeverMentioned")
    coverage = cast(dict[str, object], result["coverage"])

    assert coverage["zero_result"] == "no-indexed-matches"
    assert coverage["exhaustive"] is False
    assert "unused" not in str(coverage).lower()


def test_namespace_and_import_declarations_never_become_reference_usages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structural boilerplate is a declaration, never a usage of something."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    index = _indexed(
        tmp_path,
        {
            "a.cs": "using System.Text;\nnamespace Sample.App;\npublic class Thing { }\n",
        },
    )

    for name in ("Sample.App", "System.Text", "Sample", "App", "Text"):
        result = index.find_references(name=name)
        assert result["items"] == [], name
        assert result["possible_items"] == [], name


def test_reference_counts_agree_with_the_returned_collections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every confirmed item's match tier is reflected in the counts block."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    index = _indexed(
        tmp_path,
        {
            "ctx.cs": (
                "namespace Sample.Data;\n"
                "public class AppDbContext { public int Servers { get; set; } }\n"
            ),
            "tags.cs": (
                "namespace Sample.Shared;\n"
                "public static class Tags { public const int Servers = 1; }\n"
            ),
            "use.cs": (
                "namespace Sample.Use;\n"
                "using Sample.Data;\n"
                "public class Caller\n"
                "{\n"
                "    public int Read(AppDbContext dbContext) { return dbContext.Servers; }\n"
                "}\n"
            ),
        },
    )

    result = index.find_references(name="Servers", limit=200)
    counts = cast(dict[str, int], cast(dict[str, object], result["coverage"])["counts"])
    items = cast(list[dict[str, object]], result["items"])
    possible_items = cast(list[dict[str, object]], result["possible_items"])

    tiers = [str(item["match"]) for item in items]
    assert counts["exact"] == tiers.count("exact")
    assert counts["scoped"] == tiers.count("scoped")
    assert counts["heuristic"] == tiers.count("heuristic")
    assert counts["resolved"] == counts["exact"]
    assert counts["exact"] + counts["scoped"] + counts["heuristic"] == len(items)

    possible_tiers = [str(item["match"]) for item in possible_items]
    assert counts["ambiguous"] == possible_tiers.count("ambiguous")
    assert counts["unresolved"] == possible_tiers.count("unresolved")
    # The receiver's type is declared in the source, so this binds exactly.
    assert counts["exact"] == 1


def test_reference_ordering_is_stable_across_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated identical queries return identical, source-ordered results."""
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    index = _indexed(
        tmp_path,
        {
            "repo.cs": "namespace S;\npublic class Repo { public void Save() { } }\n",
            "use.cs": (
                "namespace S;\n"
                "public class Caller\n"
                "{\n"
                "    public void A(Repo repo) { repo.Save(); repo.Save(); }\n"
                "    public void B(Repo repo) { repo.Save(); }\n"
                "}\n"
            ),
        },
    )

    first = index.find_references(name="Save", limit=200)
    second = index.find_references(name="Save", limit=200)
    assert first == second

    items = cast(list[dict[str, object]], first["items"])
    positions = [
        (
            str(item["from_file_path"]),
            int(cast(int, item["line"])),
            int(cast(int, item["byte_column"])),
        )
        for item in items
    ]
    assert positions == sorted(positions)


def test_unicode_reference_locations_use_byte_columns(
    tmp_path: Path,
) -> None:
    """`byte_column` is a 1-based byte offset, not a character offset."""
    file_path = tmp_path / "unicode.cs"
    # "Ünïcödé" is 7 characters but 11 bytes in UTF-8.
    source = 'namespace S;\npublic class C { public void M() { var s = "Ünïcödé"; Save(); } }\n'
    file_path.write_text(source, encoding="utf-8")

    symbols = parse_file(file_path, "csharp", workspace_root=tmp_path)
    references = extract_references(file_path, "csharp", symbols, workspace_root=tmp_path)
    save = next(reference for reference in references if reference.name == "Save")

    line = source.splitlines()[save.start_line - 1]
    byte_line = line.encode("utf-8")
    assert byte_line[save.start_byte_col - 1 :].startswith(b"Save")
    # The character index is smaller, which is exactly why the distinction matters.
    assert line.index("Save") + 1 < save.start_byte_col


def test_generated_corpus_indexes_without_capture_explosion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A moderate generated corpus stays bounded in references and index size.

    A regression guard, not a benchmark: the assertions are on deterministic shape, not
    on wall-clock, which would be flaky on shared CI runners.
    """
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data-root"))
    workspace = tmp_path / "corpus"
    workspace.mkdir()
    file_count = 60
    for number in range(file_count):
        (workspace / f"Type{number:03d}.cs").write_text(
            f"namespace Corpus.Part{number % 5};\n"
            "using System.Collections.Generic;\n"
            f"public class Type{number:03d} : BaseType\n"
            "{\n"
            f"    public List<Item{number:03d}> Items {{ get; set; }}\n"
            f"    public Item{number:03d} Find(Repo repo, int id)\n"
            "    {\n"
            "        var found = repo.Lookup(id);\n"
            f"        return (Item{number:03d})found;\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )

    started = time.monotonic()
    stats = index_workspace(workspace)
    elapsed = time.monotonic() - started

    index = SymbolIndex(db_path(workspace))
    with index.transaction() as connection:
        reference_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM relations WHERE kind = 'references'"
            ).fetchone()[0]
        )
        duplicate_anchors = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT file_id, start_line, start_byte_col, to_name, COUNT(*) AS c
                    FROM relations WHERE kind = 'references'
                    GROUP BY file_id, start_line, start_byte_col, to_name
                    HAVING c > 1
                )
                """
            ).fetchone()[0]
        )

    assert stats.total_files == file_count
    # Each file has roughly a dozen usage sites; an order of magnitude more would mean
    # overlapping query patterns started multiplying rows per site.
    assert reference_count <= file_count * 25, reference_count
    assert reference_count >= file_count * 5, reference_count
    assert duplicate_anchors == 0
    assert db_path(workspace).stat().st_size <= max(1, stats.total_symbols) * 8192
    print(f"corpus: {file_count} files, {reference_count} references, {elapsed:.2f}s")
