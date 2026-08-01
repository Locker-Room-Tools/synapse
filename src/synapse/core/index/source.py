"""Bounded on-disk source slices for indexed symbols."""

from dataclasses import dataclass
from pathlib import Path

from synapse.core.models import Symbol


@dataclass(frozen=True, slots=True)
class SourceSlice:
    """A bounded run of source lines for one symbol."""

    start_line: int
    end_line: int
    text: str
    truncated: bool


def read_symbol_source(root: Path, symbol: Symbol, *, max_lines: int) -> SourceSlice | None:
    """Read at most ``max_lines`` lines of a symbol's definition from disk.

    Returns None when the file is missing, unreadable, or the stored location
    no longer fits the file (stale index).
    """
    absolute_path = root / symbol.file_path
    try:
        lines = absolute_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if symbol.start_line < 1 or symbol.start_line > len(lines):
        return None
    body_lines = lines[symbol.start_line - 1 : symbol.end_line]
    normalized_max = max(1, max_lines)
    truncated = len(body_lines) > normalized_max
    if truncated:
        body_lines = body_lines[:normalized_max]
    return SourceSlice(
        start_line=symbol.start_line,
        end_line=symbol.start_line + len(body_lines) - 1,
        text="\n".join(body_lines),
        truncated=truncated,
    )
