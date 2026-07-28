"""Normalized watch events and filesystem filtering."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from time import time

from synapse.core.config import IgnoreMatcher, active_ignore_matcher
from synapse.core.languages import detect_language
from synapse.core.workspace import require_workspace_path

_TEMP_SUFFIXES = (".tmp", ".swp", ".swo", "~")
_TEMP_NAMES = {"4913"}


class ChangeKind(StrEnum):
    """Normalized change event kinds consumed by watch core."""

    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
    RENAME = "rename"
    OVERFLOW = "overflow"


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    """A workspace-relative, backend-independent change event."""

    kind: ChangeKind
    rel_path: str | None
    timestamp: float
    old_rel_path: str | None = None


class EventNormalizer:
    """Normalize paths, ignore noise, and keep only supported source files."""

    def __init__(
        self,
        workspace_path: str | Path,
        matcher: IgnoreMatcher | None = None,
    ) -> None:
        self.root = require_workspace_path(workspace_path)
        self.matcher = matcher or active_ignore_matcher(self.root)

    def normalize_path(self, path: str | Path, *, require_language: bool = True) -> str | None:
        """Return a workspace-relative POSIX file path, or None when ignored/unsupported."""
        candidate = Path(path).expanduser()
        absolute_path = candidate if candidate.is_absolute() else self.root / candidate
        try:
            relative = absolute_path.absolute().relative_to(self.root)
        except ValueError:
            return None

        if self.matcher.ignores_relative_path(relative.parts[:-1]):
            return None
        current = self.root
        for part in relative.parts[:-1]:
            current /= part
            if current.is_symlink():
                return None
        if relative.name in _TEMP_NAMES or relative.name.endswith(_TEMP_SUFFIXES):
            return None
        if require_language and detect_language(self.root / relative) is None:
            return None
        return relative.as_posix()

    def normalize(
        self,
        kind: ChangeKind,
        path: str | Path | None,
        *,
        old_path: str | Path | None = None,
        timestamp: float | None = None,
    ) -> ChangeEvent | None:
        """Normalize one backend event into a core change event."""
        event_time = time() if timestamp is None else timestamp

        if kind is ChangeKind.OVERFLOW:
            return ChangeEvent(kind=kind, rel_path=None, timestamp=event_time)
        if path is None:
            return None

        rel_path = self.normalize_path(path, require_language=True)
        old_rel_path = (
            self.normalize_path(old_path, require_language=True) if old_path is not None else None
        )

        if rel_path is None and old_rel_path is None:
            return None

        return ChangeEvent(
            kind=kind,
            rel_path=rel_path,
            old_rel_path=old_rel_path,
            timestamp=event_time,
        )
