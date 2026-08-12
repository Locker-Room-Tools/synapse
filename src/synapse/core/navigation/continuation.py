"""Deterministic source-continuation tokens for inspection follow-ups.

A token names the next unread window of one symbol's definition:
``c_<22 handle chars>@<start_line>:<16 hex fingerprint>``. The server derives it
from index state and the agent round-trips it verbatim, like a handle. The
fingerprint binds the token to the symbol's stored span, the exact requested
start line, and the file's stored content hash: editing any component —
including only the line — invalidates the token, so the only acceptable
positions are the ones the server actually issued, and a token issued before a
re-index is rejected as stale instead of silently serving drifted lines. It is
an integrity check against accidental drift, not an authentication mechanism:
no secrets, no persisted token state.
"""

import hashlib
import re
from dataclasses import dataclass

from synapse.core.index import HANDLE_PREFIX
from synapse.core.index.handles import HANDLE_ALPHABET, HANDLE_DIGEST_CHARS
from synapse.core.models import Symbol

CONTINUATION_PREFIX = "c_"
FINGERPRINT_CHARS = 16
_TOKEN_PATTERN = re.compile(
    rf"^{CONTINUATION_PREFIX}(?P<digest>[{HANDLE_ALPHABET}]{{{HANDLE_DIGEST_CHARS}}})"
    rf"@(?P<line>[1-9][0-9]{{0,7}}):(?P<fp>[0-9a-f]{{{FINGERPRINT_CHARS}}})$"
)


@dataclass(frozen=True, slots=True)
class SourceContinuation:
    """One parsed continuation request: which symbol, from which line, bound to what."""

    handle: str
    start_line: int
    fingerprint: str


def source_fingerprint(symbol: Symbol, start_line: int, content_hash: str) -> str:
    """Fingerprint the span, exact window position, and file content of one token.

    Including ``start_line`` makes each issued token valid for its own window
    only — the line component cannot be edited without invalidating it.
    """
    material = f"{symbol.id}\n{symbol.start_line}\n{symbol.end_line}\n{start_line}\n{content_hash}"
    return hashlib.blake2b(material.encode("utf-8"), digest_size=8).hexdigest()


def continuation_token(handle: str, start_line: int, fingerprint: str) -> str:
    """Render the wire token for the window starting at ``start_line``."""
    return f"{CONTINUATION_PREFIX}{handle[len(HANDLE_PREFIX) :]}@{start_line}:{fingerprint}"


def looks_like_continuation(value: str) -> bool:
    """Whether a request key claims to be a continuation token (even if malformed).

    Keys claiming the shape but failing strict parsing are rejected with a reason
    instead of falling through to the stable-ID lookup, where the deliberate
    ``@`` marker could only ever produce a silent ``missing`` entry.
    """
    return value.startswith(CONTINUATION_PREFIX) and "@" in value


def parse_continuation(value: str) -> SourceContinuation | None:
    """Parse a strict wire token; None for anything malformed."""
    match = _TOKEN_PATTERN.fullmatch(value)
    if match is None:
        return None
    return SourceContinuation(
        handle=HANDLE_PREFIX + match.group("digest"),
        start_line=int(match.group("line")),
        fingerprint=match.group("fp"),
    )
