"""Shared payload plumbing for the two navigation response shapes."""

from synapse.core.index import symbol_handle
from synapse.core.models import Symbol


class FileTable:
    """Deduplicated file-path table; payload entries carry indexes into it.

    Built fresh on every assembly pass so dropped entries release their rows.
    """

    def __init__(self) -> None:
        self._indexes: dict[str, int] = {}

    def index(self, path: str) -> int:
        """Return the stable index for a path, adding it on first use."""
        existing = self._indexes.get(path)
        if existing is not None:
            return existing
        assigned = len(self._indexes)
        self._indexes[path] = assigned
        return assigned

    def paths(self) -> list[str]:
        """All registered paths in first-use order."""
        return list(self._indexes)


def symbol_ref(symbol: Symbol, files: FileTable) -> dict[str, object]:
    """Compact reference to a declaration: handle, name, kind, file, line."""
    return {
        "h": symbol_handle(symbol.id),
        "n": symbol.name,
        "k": str(symbol.kind),
        "f": files.index(symbol.file_path),
        "l": symbol.start_line,
    }
