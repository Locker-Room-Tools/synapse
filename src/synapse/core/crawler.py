"""File discovery and hashing for incremental indexing."""

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

from synapse.core.languages import detect_language

IGNORED_DIRECTORIES = {
    ".ai",
    ".git",
    ".idea",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


def iter_source_files(root: Path) -> Iterator[Path]:
    """Yield source files under a workspace root."""
    normalized_root = root.resolve()
    for current_root, dir_names, file_names in os.walk(normalized_root, topdown=True):
        dir_names[:] = sorted(name for name in dir_names if name not in IGNORED_DIRECTORIES)
        for file_name in sorted(file_names):
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
