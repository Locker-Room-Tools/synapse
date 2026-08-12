"""File discovery and hashing for incremental indexing."""

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

from synapse.core.config import active_ignore_matcher

# Canonical definition lives beside the verified window reads it must agree with.
from synapse.core.index.source import hash_source as hash_source
from synapse.core.languages import detect_language


def iter_source_files(root: Path) -> Iterator[Path]:
    """Yield source files under a workspace root."""
    normalized_root = root.resolve()
    matcher = active_ignore_matcher(normalized_root)

    for current_root, dir_names, file_names in os.walk(normalized_root, topdown=True):
        parent_parts = Path(current_root).relative_to(normalized_root).parts
        dir_names[:] = sorted(
            name for name in dir_names if not matcher.ignores_child(parent_parts, name)
        )
        for file_name in sorted(file_names):
            if matcher.ignores_child(parent_parts, file_name, is_dir=False):
                continue
            path = Path(current_root) / file_name
            if detect_language(path) is None:
                continue
            yield path


def hash_file(path: Path) -> str:
    """Return a stable content hash for a file."""
    digest = hashlib.sha256()

    with path.open("rb") as file_handle:
        while chunk := file_handle.read(8192):
            digest.update(chunk)
    return digest.hexdigest()
