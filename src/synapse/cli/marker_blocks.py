"""Shared editing of Synapse-managed BEGIN/END marker blocks in text files."""


def find_marker_block(
    existing: str,
    begin_marker: str,
    end_marker: str,
    *,
    partial_message: str,
) -> tuple[int, int] | None:
    """Locate the managed block, returning its (start, end-exclusive) span.

    Returns None when neither marker is present; raises ValueError with
    partial_message when only one marker is found.
    """
    start = existing.find(begin_marker)
    end = existing.find(end_marker)
    if (start != -1) != (end != -1):
        raise ValueError(partial_message)
    if start == -1:
        return None
    return start, end + len(end_marker)


def splice_marker_block(existing: str, span: tuple[int, int], *parts: str) -> str:
    """Replace the span with parts, keeping one blank line between sections."""
    start, block_end = span
    prefix = existing[:start].rstrip()
    suffix = existing[block_end:].lstrip("\n").rstrip()
    pieces = [piece for piece in (prefix, *parts, suffix) if piece]
    joined = "\n\n".join(pieces)
    return f"{joined}\n" if joined else ""


def append_marker_block(existing: str, block: str) -> str:
    """Append the block after the existing content, blank-line separated."""
    text = existing.rstrip()
    return f"{text}\n\n{block}\n" if text else f"{block}\n"
