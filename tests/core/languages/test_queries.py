"""Tests for tree-sitter query loading."""

import pytest

from synapse.core.languages import LANGUAGES
from synapse.core.languages.queries import load_query

TIER2_LANGUAGES = (
    "ada",
    "assembly",
    "cobol",
    "common_lisp",
    "crystal",
    "cuda",
    "d",
    "fortran",
    "glsl",
    "hlsl",
    "nim",
    "pascal",
    "smalltalk",
    "verilog",
    "vhdl",
    "wgsl",
)

TIER3_LANGUAGES = (
    "astro",
    "elm",
    "purescript",
    "gleam",
    "rescript",
    "scss",
    "less",
)

TIER4_LANGUAGES = (
    "fish",
    "zsh",
    "nushell",
    "awk",
    "vimscript",
    "emacs_lisp",
)

TIER5_LANGUAGES = ("gdscript", "luau", "haxe")


def test_load_query_returns_non_empty_query_text() -> None:
    """Bundled query files are loaded as text."""
    query_text = load_query("python", "symbols")

    assert "@definition.class" in query_text


def test_load_reference_queries_for_supported_languages() -> None:
    """Bundled reference queries are available for supported languages."""
    for language in LANGUAGES:
        assert "@reference" in load_query(language, "references")


def test_load_query_includes_new_scripting_language_symbols() -> None:
    """Scripting language symbol queries expose expected capture kinds."""
    assert "@definition.function" in load_query("bash", "symbols")
    assert "@definition.class" in load_query("powershell", "symbols")


def test_load_query_includes_data_ml_language_symbols() -> None:
    """Data/ML language symbol queries expose expected capture kinds."""
    assert "@definition.function" in load_query("r", "symbols")
    assert "@definition.module" in load_query("julia", "symbols")
    assert "@definition.struct" in load_query("julia", "symbols")


def test_load_query_includes_tier1_language_symbols() -> None:
    """New Tier 1 language queries expose representative symbol kinds."""
    assert "@definition.module" in load_query("haskell", "symbols")
    assert "@definition.function" in load_query("elixir", "symbols")
    assert "@definition.record" in load_query("fsharp", "symbols")
    assert "@definition.struct" in load_query("sql", "symbols")
    assert "@definition.module" in load_query("angular_template", "symbols")


@pytest.mark.parametrize("language", TIER2_LANGUAGES)
def test_tier2_queries_load(language: str) -> None:
    """Tier 2 language query pairs are bundled and expose normalized captures."""
    assert "@definition." in load_query(language, "symbols")
    assert "@reference" in load_query(language, "references")


@pytest.mark.parametrize("language", TIER3_LANGUAGES)
def test_tier3_queries_load(language: str) -> None:
    """Tier 3 language query pairs are bundled and expose normalized captures."""
    assert "@definition." in load_query(language, "symbols")
    assert "@reference" in load_query(language, "references")


@pytest.mark.parametrize("language", TIER4_LANGUAGES)
def test_tier4_queries_load(language: str) -> None:
    """Tier 4 language query pairs are bundled and expose normalized captures."""
    assert "@definition." in load_query(language, "symbols")
    assert "@reference" in load_query(language, "references")


@pytest.mark.parametrize("language", TIER5_LANGUAGES)
def test_tier5_queries_load(language: str) -> None:
    """Tier 5 language query pairs are bundled and expose normalized captures."""
    assert "@definition." in load_query(language, "symbols")
    assert "@reference" in load_query(language, "references")


def test_load_query_raises_for_missing_query_file() -> None:
    """Missing query files fail with a clear filesystem error."""
    with pytest.raises(FileNotFoundError):
        load_query("python", "missing")
