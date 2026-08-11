"""Bounded on-disk source slices for indexed symbols."""

import hashlib
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


@dataclass(frozen=True, slots=True)
class VerifiedWindow:
    """A window read that either matched the stored content hash or says why not.

    ``stale`` means the file was readable but its bytes no longer hash to the
    stored ``content_hash``; ``slice`` is then always None, so drifted content
    can never be served as a continuation of an older window.
    """

    slice: SourceSlice | None
    stale: bool


def hash_source(source_bytes: bytes) -> str:
    """Return the canonical content hash for loaded source bytes.

    The single definition shared by the indexing pipeline, the watch daemon, and
    verified window reads — the three must agree or every verification is noise.
    """
    return hashlib.sha256(source_bytes).hexdigest()


def read_symbol_source(root: Path, symbol: Symbol, *, max_lines: int) -> SourceSlice | None:
    """Read at most ``max_lines`` lines of a symbol's definition from disk.

    Returns None when the file is missing, unreadable, or the stored location
    no longer fits the file (stale index).
    """
    return read_source_window(root, symbol, start_line=symbol.start_line, max_lines=max_lines)


def read_source_window(
    root: Path, symbol: Symbol, *, start_line: int, max_lines: int
) -> SourceSlice | None:
    """Read at most ``max_lines`` lines of a symbol's definition from ``start_line``.

    The window is clipped to the symbol's stored span; ``truncated`` means stored
    body remains beyond the returned window. Returns None when the file is
    missing, unreadable, or ``start_line`` no longer fits the file (stale index).
    """
    absolute_path = root / symbol.file_path
    try:
        lines = absolute_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return _window_from_lines(lines, symbol, start_line=start_line, max_lines=max_lines)


def read_verified_source_window(
    root: Path, symbol: Symbol, *, start_line: int, max_lines: int, content_hash: str
) -> VerifiedWindow:
    """Read a window only if the on-disk bytes still hash to ``content_hash``.

    The bytes are read once and both hashed and sliced, so the verification and
    the returned text cannot disagree. A mismatch returns ``stale`` instead of a
    slice: a continuation must never splice lines from a newer file version onto
    a head slice served from an older one.
    """
    absolute_path = root / symbol.file_path
    try:
        source_bytes = absolute_path.read_bytes()
    except OSError:
        return VerifiedWindow(slice=None, stale=False)
    if hash_source(source_bytes) != content_hash:
        return VerifiedWindow(slice=None, stale=True)
    lines = source_bytes.decode("utf-8", errors="replace").splitlines()
    window = _window_from_lines(lines, symbol, start_line=start_line, max_lines=max_lines)
    return VerifiedWindow(slice=window, stale=False)


def _window_from_lines(
    lines: list[str], symbol: Symbol, *, start_line: int, max_lines: int
) -> SourceSlice | None:
    if start_line < 1 or start_line > len(lines):
        return None
    body_lines = lines[start_line - 1 : symbol.end_line]
    normalized_max = max(1, max_lines)
    truncated = len(body_lines) > normalized_max
    if truncated:
        body_lines = body_lines[:normalized_max]
    return SourceSlice(
        start_line=start_line,
        end_line=start_line + len(body_lines) - 1,
        text="\n".join(body_lines),
        truncated=truncated,
    )
