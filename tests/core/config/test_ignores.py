"""Tests for gitignore-style pattern compilation and matching."""

import pytest

from synapse.core.config import (
    ConfigScope,
    IgnoreMatcher,
    normalize_ignore_entry,
    parse_ignore_text,
    rule_text_for_json_entry,
    validate_ignore_pattern,
)


def _matcher(*lines: str) -> IgnoreMatcher:
    rules, _ = parse_ignore_text(
        "\n".join(lines), scope=ConfigScope.PROJECT, origin=".synapseignore"
    )
    return IgnoreMatcher.from_rules(rules)


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


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("node_modules", "node_modules/"),
        ("/build", "/build/"),
        ("src/generated", "/src/generated/"),
    ],
)
def test_rule_text_for_json_entry_keeps_legacy_entries_directory_only(
    entry: str, expected: str
) -> None:
    """Legacy entries always named directories, so no file becomes newly ignored."""
    assert rule_text_for_json_entry(entry) == expected


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

    assert matcher.ignores_relative_path(("node_modules",), is_dir=True)
    assert matcher.ignores_relative_path(("pkg", "node_modules", "deep.py"))
    assert matcher.ignores_relative_path(("src", "generated", "x.py"))
    assert not matcher.ignores_relative_path(("pkg", "src", "generated", "x.py"))


def test_ignores_relative_path_accepts_an_empty_path() -> None:
    """The workspace root itself is never ignored."""
    assert not IgnoreMatcher.from_entries(["build"]).ignores_relative_path(())
    assert not IgnoreMatcher.from_entries([]).ignores_relative_path(("build",))


def test_legacy_entries_never_ignore_a_file_of_the_same_name() -> None:
    """Legacy entries are directory-only, so a file literally named 'build' survives."""
    matcher = IgnoreMatcher.from_entries(["build"])

    assert matcher.ignores_child((), "build", is_dir=True)
    assert not matcher.ignores_child((), "build", is_dir=False)


@pytest.mark.parametrize(
    ("pattern", "name", "is_dir", "ignored"),
    [
        ("*.min.js", "app.min.js", False, True),
        ("*.min.js", "app.js", False, False),
        ("test_?.py", "test_a.py", False, True),
        ("test_?.py", "test_ab.py", False, False),
        ("[Bb]uild/", "Build", True, True),
        ("[Bb]uild/", "build", True, True),
        ("[Bb]uild/", "build", False, False),
    ],
)
def test_globs_and_file_patterns(pattern: str, name: str, is_dir: bool, ignored: bool) -> None:
    """Globs match file names, and a trailing slash keeps a rule directory-only."""
    assert _matcher(pattern).ignores_child((), name, is_dir=is_dir) is ignored


def test_anchoring_distinguishes_rooted_from_floating_patterns() -> None:
    """A leading slash pins a rule to the root; a bare name floats to any depth."""
    rooted = _matcher("/dist/")
    assert rooted.ignores_child((), "dist")
    assert not rooted.ignores_child(("pkg",), "dist")

    floating = _matcher("dist/")
    assert floating.ignores_child((), "dist")
    assert floating.ignores_child(("pkg",), "dist")


def test_double_star_patterns() -> None:
    """'**' spans directory levels; a leading '**/' is explicitly un-anchored."""
    assert _matcher("**/logs/").ignores_child(("a", "b"), "logs")
    assert _matcher("a/**/b/").ignores_child(("a", "deep", "deeper"), "b")
    assert _matcher("a/**/b/").ignores_child(("a",), "b")


def test_last_matching_rule_wins_in_both_directions() -> None:
    """Ordering, not specificity, decides — a later rule overrides an earlier one."""
    reincluded = _matcher("*.py", "!keep.py")
    assert not reincluded.ignores_child((), "keep.py", is_dir=False)
    assert reincluded.ignores_child((), "other.py", is_dir=False)

    reversed_order = _matcher("!keep.py", "*.py")
    assert reversed_order.ignores_child((), "keep.py", is_dir=False)


def test_negation_under_an_ignored_directory_is_inert() -> None:
    """Git parity: nothing under a pruned directory can be re-included."""
    matcher = _matcher("build/", "!build/keep.py")

    assert matcher.ignores_relative_path(("build", "keep.py"))


def test_git_directory_cannot_be_reincluded() -> None:
    """'.git' is pinned because un-ignoring it costs unboundedly and buys nothing."""
    matcher = _matcher("!.git/")

    assert matcher.ignores_child((), ".git", is_dir=True)


def test_comments_and_blank_lines_are_skipped() -> None:
    """Blank lines and '#' comments produce no rules and no problems."""
    rules, problems = parse_ignore_text(
        "# a comment\n\n   \nbuild/\n", scope=ConfigScope.PROJECT, origin="f"
    )

    assert [rule.pattern for rule in rules] == ["build/"]
    assert problems == ()


def test_escaped_leading_characters_are_literal() -> None:
    """A backslash escape makes '#' and '!' literal rather than syntax."""
    assert _matcher(r"\#notacomment").ignores_child((), "#notacomment", is_dir=False)
    assert _matcher(r"\!literal").ignores_child((), "!literal", is_dir=False)


def test_crlf_payload_and_trailing_whitespace() -> None:
    """CRLF line endings parse, and unescaped trailing spaces are stripped."""
    rules, problems = parse_ignore_text(
        "build/\r\ndist/  \r\n", scope=ConfigScope.PROJECT, origin="f"
    )

    assert [rule.pattern for rule in rules] == ["build/", "dist/"]
    assert problems == ()
    assert IgnoreMatcher.from_rules(rules).ignores_child((), "dist", is_dir=True)


def test_unicode_names_match() -> None:
    """Patterns and directory names are compared as text, not bytes."""
    assert _matcher("логи/").ignores_child((), "логи", is_dir=True)
    assert _matcher(".venv-ä/").ignores_child((), ".venv-ä", is_dir=True)


def test_rules_carry_provenance() -> None:
    """Each rule records its scope, origin file, and line so callers can report it."""
    rules, _ = parse_ignore_text(
        "# header\nbuild/\n!build/keep.py\n", scope=ConfigScope.GLOBAL, origin="ignore"
    )

    assert [(rule.line, rule.scope, rule.origin) for rule in rules] == [
        (2, ConfigScope.GLOBAL, "ignore"),
        (3, ConfigScope.GLOBAL, "ignore"),
    ]
    assert rules[0].directory_only
    assert rules[1].negated


@pytest.mark.parametrize("text", ["[unterminated", "!"])
def test_unusable_lines_are_skipped_without_failing_the_file(text: str) -> None:
    """One bad line never disarms the rest — that could silently un-ignore a build tree."""
    rules, problems = parse_ignore_text(
        f"build/\n{text}\ndist/\n", scope=ConfigScope.PROJECT, origin="f"
    )

    assert [rule.pattern for rule in rules] == ["build/", "dist/"]
    assert [(problem.line, problem.text) for problem in problems] == [(2, text)]


@pytest.mark.parametrize("pattern", ["*.js", "!x", "/a/b/", "build/", "docs/**"])
def test_validate_ignore_pattern_accepts_gitignore_forms(pattern: str) -> None:
    """The write path accepts everything the matcher understands, verbatim."""
    assert validate_ignore_pattern(pattern, source="test") == pattern


@pytest.mark.parametrize(
    ("pattern", "match"),
    [
        ("", "must not be empty"),
        ("   ", "must not be empty"),
        ("/", "must not be empty"),
        ("!", "must not be empty"),
        ("# comment", "must not be comments"),
        ("C:\\projects", "absolute paths"),
        ("..", "'.' and '..'"),
        ("../up", "'.' and '..'"),
        ("a/../b", "'.' and '..'"),
        ("[unterminated", "invalid pattern"),
    ],
)
def test_validate_ignore_pattern_rejects_unusable_input(pattern: str, match: str) -> None:
    """Rejections name the value, the reason, and the accepted forms."""
    with pytest.raises(ValueError, match=match) as excinfo:
        validate_ignore_pattern(pattern, source="the directories argument")

    assert "the directories argument" in str(excinfo.value)
