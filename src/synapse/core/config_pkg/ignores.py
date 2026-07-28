"""Directory ignore matching shared by the crawler and the watch layer."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import NoReturn

_GLOB_CHARACTERS = "*?[]"
_RESERVED_SEGMENTS = frozenset({".", ".."})
_ACCEPTED_FORMS = (
    "Use a bare directory name matched at any depth ('node_modules'), a root-anchored "
    "name ('/build'), or a workspace-relative path ('src/generated')."
)


def _reject(value: object, reason: str, *, source: str) -> NoReturn:
    msg = f"Invalid ignored directory {value!r} in {source}: {reason}. {_ACCEPTED_FORMS}"
    raise ValueError(msg)


def normalize_ignore_entry(value: object, *, source: str) -> str:
    """Return the canonical form of one ignored directory entry.

    Matching is case-sensitive because entries are compared against real directory names.
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


@dataclass(frozen=True, slots=True)
class IgnoreMatcher:
    """Decide whether a directory is ignored, by bare name or by anchored path."""

    names: frozenset[str]
    anchored_paths: frozenset[tuple[str, ...]]

    @classmethod
    def from_entries(cls, entries: Iterable[str]) -> IgnoreMatcher:
        """Build a matcher from entries already canonicalized by normalize_ignore_entry."""
        names: set[str] = set()
        anchored_paths: set[tuple[str, ...]] = set()
        for entry in entries:
            if "/" in entry:
                anchored_paths.add(tuple(entry.lstrip("/").split("/")))
            else:
                names.add(entry)
        return cls(names=frozenset(names), anchored_paths=frozenset(anchored_paths))

    def ignores_child(self, parent_parts: tuple[str, ...], name: str) -> bool:
        """Return whether a directory named `name` under `parent_parts` is ignored."""
        if name in self.names:
            return True
        return bool(self.anchored_paths) and (*parent_parts, name) in self.anchored_paths

    def ignores_relative_path(self, parts: tuple[str, ...]) -> bool:
        """Return whether any directory component of a workspace-relative path is ignored."""
        return any(self.ignores_child(parts[:depth], name) for depth, name in enumerate(parts))
