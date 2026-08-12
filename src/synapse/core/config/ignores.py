"""Gitignore-style ignore matching shared by the crawler and the watch layer.

Rules are ordered and the last matching rule wins, so a later `!pattern` re-includes what an
earlier rule ignored. Paths are decided component by component from the workspace root down, the
way git itself decides them: the first component that resolves to ignored wins and nothing below it
can be re-included. That is what makes `os.walk` pruning equivalent to evaluating full paths, and it
is why `!build/keep.py` is inert when `build/` is ignored.

`pathspec` is used purely as the pattern-to-regex compiler. The ordered rule list lives here because
it is what carries per-rule provenance (scope, origin file, line number).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn

from pathspec.patterns.gitignore import GitIgnorePatternError
from pathspec.patterns.gitignore.spec import GitIgnoreSpecPattern

_GLOB_CHARACTERS = "*?[]"
_RESERVED_SEGMENTS = frozenset({".", ".."})
_ACCEPTED_FORMS = (
    "Use a bare directory name matched at any depth ('node_modules'), a root-anchored "
    "name ('/build'), or a workspace-relative path ('src/generated')."
)
_ACCEPTED_PATTERN_FORMS = (
    "Use a bare name matched at any depth ('node_modules'), a trailing slash for directories only "
    "('build/'), a leading slash to anchor at the workspace root ('/dist'), a glob ('*.min.js'), "
    "or a leading '!' to re-include ('!src/vendor/keep.js')."
)

_HARD_IGNORED_DIRECTORY_NAMES = frozenset({".git"})
"""Directory names no rule can re-include. Un-ignoring these has unbounded cost and no use."""


class ConfigScope(StrEnum):
    """Layer a rule comes from. Declaration order is evaluation order."""

    BUILT_IN = "built-in"
    GLOBAL = "global"
    PROJECT = "project"


def _reject(value: object, reason: str, *, source: str, forms: str = _ACCEPTED_FORMS) -> NoReturn:
    msg = f"Invalid ignored directory {value!r} in {source}: {reason}. {forms}"
    raise ValueError(msg)


def normalize_ignore_entry(value: object, *, source: str) -> str:
    """Return the canonical form of one legacy ignored_directories entry.

    Only the JSON config layers use this. Matching is case-sensitive because entries are compared
    against real directory names.
    """
    if not isinstance(value, str):
        _reject(value, "entries must be strings", source=source)

    candidate = value.strip().replace("\\", "/")
    if any(character in candidate for character in _GLOB_CHARACTERS):
        _reject(value, "glob patterns are not supported", source=source)
    if len(candidate) >= 2 and candidate[1] == ":":
        _reject(value, "absolute paths are not allowed", source=source)

    anchored = candidate.startswith("/")
    while candidate.startswith("./"):
        candidate = candidate[2:]

    segments = [segment for segment in candidate.split("/") if segment]
    if not segments:
        _reject(value, "entries must not be empty", source=source)
    for segment in segments:
        if segment in _RESERVED_SEGMENTS:
            _reject(value, "path segments '.' and '..' are not allowed", source=source)

    normalized = "/".join(segments)
    return f"/{normalized}" if anchored else normalized


def validate_ignore_pattern(value: object, *, source: str) -> str:
    """Return one caller-supplied gitignore pattern, validated but otherwise verbatim.

    Globs, a trailing slash, and a leading '!' are all accepted here; only forms that cannot mean
    anything inside a workspace are rejected.
    """
    if not isinstance(value, str):
        _reject(value, "patterns must be strings", source=source, forms=_ACCEPTED_PATTERN_FORMS)

    candidate = value.strip().replace("\\", "/")
    if not candidate or candidate in {"!", "/"}:
        _reject(value, "patterns must not be empty", source=source, forms=_ACCEPTED_PATTERN_FORMS)
    if candidate.startswith("#"):
        _reject(
            value, "patterns must not be comments", source=source, forms=_ACCEPTED_PATTERN_FORMS
        )
    if len(candidate) >= 2 and candidate[1] == ":":
        _reject(
            value, "absolute paths are not allowed", source=source, forms=_ACCEPTED_PATTERN_FORMS
        )

    body = candidate[1:] if candidate.startswith("!") else candidate
    if any(segment in _RESERVED_SEGMENTS for segment in body.split("/")):
        _reject(
            value,
            "path segments '.' and '..' are not allowed",
            source=source,
            forms=_ACCEPTED_PATTERN_FORMS,
        )

    try:
        compile_pattern(candidate, scope=ConfigScope.PROJECT, origin=source, line=0)
    except ValueError as exc:
        _reject(value, str(exc), source=source, forms=_ACCEPTED_PATTERN_FORMS)
    return candidate


def rule_text_for_json_entry(entry: str) -> str:
    """Return the gitignore rule equivalent to one normalized ignored_directories entry.

    Every legacy entry named a directory, so every generated rule is directory-only. An entry was
    anchored either by a leading slash or by having several segments, and the leading slash on the
    generated rule makes both cases explicit.
    """
    stripped = entry.lstrip("/")
    anchored = entry.startswith("/") or "/" in stripped
    return f"/{stripped}/" if anchored else f"{stripped}/"


@dataclass(frozen=True, slots=True)
class IgnoreRule:
    """One compiled gitignore-style rule and where it came from."""

    pattern: str
    regex: re.Pattern[str]
    negated: bool
    directory_only: bool
    scope: ConfigScope
    origin: str
    line: int


@dataclass(frozen=True, slots=True)
class IgnoreProblem:
    """One source line that could not be compiled and was skipped."""

    origin: str
    line: int
    text: str
    reason: str


def _is_skippable(stripped: str) -> bool:
    return not stripped or stripped.startswith("#")


def compile_pattern(
    text: str,
    *,
    scope: ConfigScope,
    origin: str,
    line: int,
) -> IgnoreRule | None:
    """Compile one pattern line, or None for a blank or comment line.

    Raises ValueError when the line is meant as a pattern but cannot be compiled.
    """
    stripped = text.strip()
    if _is_skippable(stripped):
        return None

    try:
        compiled = GitIgnoreSpecPattern(text)
    except GitIgnorePatternError as exc:
        msg = f"invalid pattern: {exc}"
        raise ValueError(msg) from exc

    if compiled.include is None or compiled.regex is None:
        # pathspec returns include=None instead of raising for lines it cannot use, such as an
        # unterminated character class. Left alone that would silently become a no-op line.
        msg = "invalid pattern: matches nothing"
        raise ValueError(msg)

    return IgnoreRule(
        pattern=stripped,
        regex=re.compile(compiled.regex.pattern),
        negated=not compiled.include,
        directory_only=text.rstrip().endswith("/"),
        scope=scope,
        origin=origin,
        line=line,
    )


def parse_ignore_text(
    text: str,
    *,
    scope: ConfigScope,
    origin: str,
) -> tuple[tuple[IgnoreRule, ...], tuple[IgnoreProblem, ...]]:
    """Parse an ignore file payload into ordered rules plus the lines that were skipped.

    An unusable line never fails the file: rejecting everything over one typo could silently
    un-ignore a directory the rest of the file depends on.
    """
    rules: list[IgnoreRule] = []
    problems: list[IgnoreProblem] = []

    for number, raw_line in enumerate(text.splitlines(), start=1):
        try:
            rule = compile_pattern(raw_line, scope=scope, origin=origin, line=number)
        except ValueError as exc:
            problems.append(
                IgnoreProblem(origin=origin, line=number, text=raw_line.strip(), reason=str(exc))
            )
            continue
        if rule is not None:
            rules.append(rule)

    return tuple(rules), tuple(problems)


@dataclass(frozen=True, slots=True)
class IgnoreMatcher:
    """Decide whether a path is ignored. Rules are ordered and the last match wins.

    Building a matcher compiles nothing: rules arrive pre-compiled. If a workspace ever carries
    enough rules for the per-layer file reads to show up in a profile, cache the loaded layers on
    `(root, (path, st_mtime_ns, st_size) per layer)` rather than caching matchers here.
    """

    rules: tuple[IgnoreRule, ...]

    @classmethod
    def from_rules(cls, rules: Iterable[IgnoreRule]) -> IgnoreMatcher:
        """Build a matcher from rules already in evaluation order."""
        return cls(rules=tuple(rules))

    @classmethod
    def from_entries(cls, entries: Iterable[str]) -> IgnoreMatcher:
        """Build a matcher from legacy ignored_directories entries, in sorted order.

        Legacy entries carry no ordering and no negation, so any stable order behaves the same.
        """
        rules: list[IgnoreRule] = []
        for entry in sorted(set(entries)):
            rule = compile_pattern(
                rule_text_for_json_entry(entry),
                scope=ConfigScope.PROJECT,
                origin="ignored_directories",
                line=0,
            )
            if rule is not None:
                rules.append(rule)
        return cls.from_rules(rules)

    def match(
        self,
        parent_parts: tuple[str, ...],
        name: str,
        *,
        is_dir: bool,
    ) -> IgnoreRule | None:
        """Return the rule that decides this entry, or None when no rule matches it.

        Directory candidates carry a trailing slash, which is exactly what a directory-only
        pattern's regex requires, so `directory_only` needs no separate test here.
        """
        candidate = "/".join((*parent_parts, name))
        if is_dir:
            candidate = f"{candidate}/"

        decision: IgnoreRule | None = None
        for rule in self.rules:
            if rule.regex.match(candidate):
                decision = rule
        return decision

    def ignores_child(
        self,
        parent_parts: tuple[str, ...],
        name: str,
        *,
        is_dir: bool = True,
    ) -> bool:
        """Return whether one entry under `parent_parts` is ignored.

        Assumes no ancestor is itself ignored, which `os.walk` pruning already guarantees.
        """
        if is_dir and name in _HARD_IGNORED_DIRECTORY_NAMES:
            return True
        decision = self.match(parent_parts, name, is_dir=is_dir)
        return decision is not None and not decision.negated

    def ignores_relative_path(self, parts: tuple[str, ...], *, is_dir: bool = False) -> bool:
        """Return whether a relative path is ignored, deciding it component by component."""
        for depth in range(len(parts) - 1):
            if self.ignores_child(parts[:depth], parts[depth], is_dir=True):
                return True
        return bool(parts) and self.ignores_child(parts[:-1], parts[-1], is_dir=is_dir)
