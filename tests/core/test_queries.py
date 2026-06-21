"""Tests for tree-sitter query loading."""

import pytest

from synapse.core.queries import load_query


def test_load_query_returns_non_empty_query_text() -> None:
    """Bundled query files are loaded as text."""
    query_text = load_query("python", "symbols")

    assert "@definition.class" in query_text


def test_load_reference_queries_for_supported_languages() -> None:
    """Bundled reference queries are available for supported languages."""
    for language in (
        "c",
        "cpp",
        "python",
        "csharp",
        "dart",
        "go",
        "java",
        "javascript",
        "kotlin",
        "lua",
        "php",
        "rust",
        "ruby",
        "scala",
        "swift",
        "typescript",
        "tsx",
    ):
        assert "@reference" in load_query(language, "references")


def test_load_query_raises_for_missing_query_file() -> None:
    """Missing query files fail with a clear filesystem error."""
    with pytest.raises(FileNotFoundError):
        load_query("python", "missing")
