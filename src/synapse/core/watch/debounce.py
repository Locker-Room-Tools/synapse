"""Debounce, coalesce, and batch normalized watch events."""

from dataclasses import dataclass, field
from time import time

from synapse.core.watch.events import ChangeEvent, ChangeKind


@dataclass(frozen=True, slots=True)
class WatchBatch:
    """A bounded set of file operations for the single writer."""

    reindex_paths: list[str] = field(default_factory=list)
    remove_paths: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        """Return whether the batch contains no work."""
        return not self.reindex_paths and not self.remove_paths


@dataclass(slots=True)
class _PendingChange:
    intent: str
    first_seen: float
    last_seen: float


class CoalescingBuffer:
    """Per-path debounce buffer with latest-intent-wins coalescing."""

    def __init__(self, *, debounce_ms: int, max_latency_ms: int, batch_size: int) -> None:
        self.debounce_s = debounce_ms / 1000
        self.max_latency_s = max_latency_ms / 1000
        self.batch_size = max(1, batch_size)
        self._pending: dict[str, _PendingChange] = {}

    def add(self, event: ChangeEvent, *, now: float | None = None) -> None:
        """Add one normalized event to the coalescing map."""
        event_time = event.timestamp if now is None else now
        if event.kind is ChangeKind.OVERFLOW:
            return
        if event.kind is ChangeKind.RENAME and event.old_rel_path is not None:
            self._set_intent(event.old_rel_path, "remove", event_time)
        if event.rel_path is None:
            return
        if event.kind is ChangeKind.DELETE:
            self._set_intent(event.rel_path, "remove", event_time)
        else:
            self._set_intent(event.rel_path, "reindex", event_time)

    def _set_intent(self, rel_path: str, intent: str, event_time: float) -> None:
        current = self._pending.get(rel_path)
        if current is None:
            self._pending[rel_path] = _PendingChange(intent, event_time, event_time)
            return
        self._pending[rel_path] = _PendingChange(intent, current.first_seen, event_time)

    def flush_ready(self, *, now: float | None = None, force: bool = False) -> WatchBatch:
        """Return ready work whose debounce window elapsed or latency cap was hit."""
        current_time = time() if now is None else now
        ready: list[str] = []
        for rel_path, pending in self._pending.items():
            quiet = current_time - pending.last_seen >= self.debounce_s
            stale = current_time - pending.first_seen >= self.max_latency_s
            if force or quiet or stale or len(ready) >= self.batch_size:
                ready.append(rel_path)
            if len(ready) >= self.batch_size:
                break

        reindex_paths: list[str] = []
        remove_paths: list[str] = []
        for rel_path in sorted(ready):
            pending = self._pending.pop(rel_path)
            if pending.intent == "remove":
                remove_paths.append(rel_path)
            else:
                reindex_paths.append(rel_path)
        return WatchBatch(reindex_paths=reindex_paths, remove_paths=remove_paths)

    def pending_count(self) -> int:
        """Return the number of coalesced pending paths."""
        return len(self._pending)
