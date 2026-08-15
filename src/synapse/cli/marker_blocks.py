"""Shared editing of Synapse-managed blocks in text files.

Two block anchors exist: explicit BEGIN/END comment markers (TOML MCP configs,
plus legacy instruction installs) and heading-anchored markdown blocks that
carry no markup beyond the snippet's own heading.
"""


def find_heading_block(existing: str, headings: tuple[str, ...]) -> tuple[int, int] | None:
    """Locate a managed markdown block anchored on one of its known heading lines.

    The block spans from the first matching heading line to the next line that
    starts a heading of any level, or the end of the text. Returns None when no
    heading matches as a complete line.
    """
    for heading in headings:
        start = _line_start(existing, heading)
        if start == -1:
            continue
        boundary = existing.find("\n#", start + len(heading))
        return start, len(existing) if boundary == -1 else boundary
    return None


def _line_start(text: str, line: str) -> int:
    position = 0 if text.startswith(line) else text.find("\n" + line)
    if position == -1:
        return -1
    start = position if position == 0 else position + 1
    end = start + len(line)
    if end < len(text) and text[end] != "\n":
        return -1
    return start


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
