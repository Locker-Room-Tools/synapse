"""SourceFile model: a record of one indexed file."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceFile:
    """An indexed source file and its incremental-indexing metadata."""

    id: str
    path: str
    language: str
    project_root: str | None
    content_hash: str
    indexed_at: str