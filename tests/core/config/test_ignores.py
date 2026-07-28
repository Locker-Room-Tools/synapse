"""Tests for ignored directory normalization and matching."""

import pytest

from synapse.core.config import IgnoreMatcher, normalize_ignore_entry


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("node_modules", "node_modules"),
        ("  node_modules  ", "node_modules"),
        ("./src/generated/", "src/generated"),
        ("src\\generated", "src/generated"),
        ("a//b", "a/b"),
        ("/build", "/build"),
        ("/build/", "/build"),
        ("././docs/build", "docs/build"),
    ],
)
def test_normalize_ignore_entry_canonicalizes_accepted_forms(raw: str, expected: str) -> None:
    """Separators, redundant prefixes, and whitespace collapse to a canonical form."""
    assert normalize_ignore_entry(raw, source="test") == expected


def test_normalize_ignore_entry_is_idempotent() -> None:
    """Normalizing an already-canonical entry leaves it unchanged."""
    for raw in ("node_modules", "/build", "src/generated"):
        once = normalize_ignore_entry(raw, source="test")
        assert normalize_ignore_entry(once, source="test") == once


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ("", "must not be empty"),
        ("   ", "must not be empty"),
        ("/", "must not be empty"),
        ("C:\\projects", "absolute paths"),
        (".", "'.' and '..'"),
        ("..", "'.' and '..'"),
        ("../up", "'.' and '..'"),
        ("a/../b", "'.' and '..'"),
        ("*.py", "glob patterns"),
        ("src/*", "glob patterns"),
        ("build?", "glob patterns"),
    ],
)
def test_normalize_ignore_entry_rejects_unsupported_input(raw: str, match: str) -> None:
    """Every rejection names the offending value, the reason, and the accepted forms."""
    with pytest.raises(ValueError, match=match) as excinfo:
        normalize_ignore_entry(raw, source="the directories argument")

    message = str(excinfo.value)
    assert "the directories argument" in message
    assert "node_modules" in message


def test_normalize_ignore_entry_rejects_non_strings() -> None:
    """Non-string entries are rejected rather than coerced."""
    with pytest.raises(ValueError, match="entries must be strings"):
        normalize_ignore_entry(7, source="test")


def test_from_entries_splits_bare_names_from_anchored_paths() -> None:
    """A slash anywhere in an entry makes it root-anchored."""
    matcher = IgnoreMatcher.from_entries(["node_modules", "/build", "src/generated"])

    assert matcher.names == frozenset({"node_modules"})
    assert matcher.anchored_paths == frozenset({("build",), ("src", "generated")})


def test_ignores_child_matches_bare_names_at_any_depth() -> None:
    """A bare name matches wherever it appears in the tree."""
    matcher = IgnoreMatcher.from_entries(["node_modules"])

    assert matcher.ignores_child((), "node_modules")
    assert matcher.ignores_child(("pkg", "vendor"), "node_modules")
    assert not matcher.ignores_child((), "node_modules_extra")


def test_ignores_child_anchors_multi_segment_paths_to_the_root() -> None:
    """An anchored path matches only at its exact position under the workspace root."""
    matcher = IgnoreMatcher.from_entries(["src/generated"])

    assert matcher.ignores_child(("src",), "generated")
    assert not matcher.ignores_child(("pkg", "src"), "generated")
    assert not matcher.ignores_child((), "generated")


def test_ignores_child_anchors_leading_slash_names_to_the_root() -> None:
    """'/build' matches only the top-level build directory."""
    matcher = IgnoreMatcher.from_entries(["/build"])

    assert matcher.ignores_child((), "build")
    assert not matcher.ignores_child(("pkg",), "build")


def test_ignores_child_is_case_sensitive() -> None:
    """Entries are compared against real directory names without case folding."""
    matcher = IgnoreMatcher.from_entries(["node_modules"])

    assert not matcher.ignores_child((), "Node_Modules")


def test_ignores_relative_path_matches_any_ancestor() -> None:
    """A path is ignored when any of its directory components is ignored."""
    matcher = IgnoreMatcher.from_entries(["node_modules", "src/generated"])

    assert matcher.ignores_relative_path(("node_modules",))
    assert matcher.ignores_relative_path(("pkg", "node_modules", "deep"))
    assert matcher.ignores_relative_path(("src", "generated"))
    assert not matcher.ignores_relative_path(("pkg", "src", "generated"))


def test_ignores_relative_path_accepts_an_empty_path() -> None:
    """The workspace root itself is never ignored."""
    assert not IgnoreMatcher.from_entries(["build"]).ignores_relative_path(())
    assert not IgnoreMatcher.from_entries([]).ignores_relative_path(("build",))
