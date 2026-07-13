"""Watch backend interface and stdlib polling backend marker.

Native OS event watching is intentionally deferred; the production supervisor currently uses
the dependency-free polling backend and full reconciliation sweeps.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class RawEventKind(StrEnum):
    """Backend-level filesystem event kinds."""

    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    OVERFLOW = "overflow"


@dataclass(frozen=True, slots=True)
class RawEvent:
    """One event emitted by a concrete filesystem watcher."""

    path: Path
    kind: RawEventKind
    timestamp: float
    old_path: Path | None = None


class WatchBackend(Protocol):
    """Interface hiding concrete OS-watch implementations."""

    name: str
    degraded: bool

    def start(self) -> None:
        """Start capturing filesystem changes."""

    def stop(self) -> None:
        """Stop capturing filesystem changes."""


@dataclass(slots=True)
class PollingWatchBackend:
    """Dependency-free backend descriptor for reconcile-only polling mode."""

    root: Path
    name: str = "polling"
    degraded: bool = False

    def start(self) -> None:
        """Polling mode has no native watcher to arm."""

    def stop(self) -> None:
        """Polling mode has no native watcher to stop."""
