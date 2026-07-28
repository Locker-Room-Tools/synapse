"""Parser coverage for Tier 5 language support."""

from pathlib import Path

import pytest

from synapse.core.parser import extract_references, parse_file

TIER5_SAMPLES = (
    (
        "gdscript",
        "sample.gd",
        "func helper():\n    pass\nfunc main():\n    helper()\n",
        ("function", "main"),
        "helper",
    ),
    (
        "luau",
        "sample.luau",
        "local function helper() end\nlocal function main() helper() end\n",
        ("function", "main"),
        "helper",
    ),
    (
        "haxe",
        "Main.hx",
        "class Main { static function helper() {} static function main() { helper(); } }\n",
        ("function", "main"),
        "helper",
    ),
)


@pytest.mark.parametrize(
    ("language", "file_name", "source", "expected_symbol", "expected_reference"),
    TIER5_SAMPLES,
)
def test_parse_file_extracts_tier5_symbols_and_references(
    tmp_path: Path,
    language: str,
    file_name: str,
    source: str,
    expected_symbol: tuple[str, str],
    expected_reference: str,
) -> None:
    """Tier 5 query files extract representative symbols and references."""
    file_path = tmp_path / file_name
    file_path.write_text(source, encoding="utf-8")

    symbols = parse_file(file_path, language, workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name) for symbol in symbols}

    assert expected_symbol in by_kind_name

    references = extract_references(file_path, language, symbols)
    reference_names = {reference.name for reference in references}
    assert expected_reference in reference_names
