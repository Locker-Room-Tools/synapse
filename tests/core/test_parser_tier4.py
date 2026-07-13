"""Parser coverage for Tier 4 language support."""

from pathlib import Path

import pytest

from synapse.core.parser import extract_references, parse_file

TIER4_SAMPLES = (
    (
        "fish",
        "sample.fish",
        "function main\n"
        "  set name Ada\n"
        "  helper $name\n"
        "end\n",
        ("function", "main"),
        "helper",
    ),
    (
        "zsh",
        "sample.zsh",
        "helper() { echo \"$1\"; }\n"
        "main() { helper \"$name\"; }\n",
        ("function", "main"),
        "helper",
    ),
    (
        "nushell",
        "sample.nu",
        "def helper [] {}\n"
        "def main [] { helper }\n",
        ("function", "main"),
        "helper",
    ),
    (
        "awk",
        "sample.awk",
        "function helper(x) { return x }\n"
        "function main() { helper(value) }\n",
        ("function", "main"),
        "helper",
    ),
    (
        "vimscript",
        "sample.vim",
        "function! Helper()\n"
        "endfunction\n"
        "function! Main()\n"
        "  call Helper()\n"
        "endfunction\n",
        ("function", "Main"),
        "Helper",
    ),
    (
        "emacs_lisp",
        "sample.el",
        "(defun helper (x) x)\n"
        "(defun main () (helper 1))\n",
        ("function", "main"),
        "helper",
    ),
)


@pytest.mark.parametrize(
    ("language", "file_name", "source", "expected_symbol", "expected_reference"),
    TIER4_SAMPLES,
)
def test_parse_file_extracts_tier4_symbols_and_references(
    tmp_path: Path,
    language: str,
    file_name: str,
    source: str,
    expected_symbol: tuple[str, str],
    expected_reference: str,
) -> None:
    """Tier 4 query files extract representative symbols and references."""
    file_path = tmp_path / file_name
    file_path.write_text(source, encoding="utf-8")

    symbols = parse_file(file_path, language, workspace_root=tmp_path)
    by_kind_name = {(str(symbol.kind), symbol.name) for symbol in symbols}

    assert expected_symbol in by_kind_name

    references = extract_references(file_path, language, symbols)
    reference_names = {reference.name for reference in references}
    assert expected_reference in reference_names