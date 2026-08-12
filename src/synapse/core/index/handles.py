"""Deterministic compact symbol handles derived from stable symbol IDs.

The wire format is fixed: `HANDLE_PREFIX` plus exactly `HANDLE_DIGEST_CHARS` base64url
characters. `HANDLE_ALPHABET` is the one definition of that character set, consumed by
both the Python predicate and the SQL constraint so the two cannot drift.

The format is all a database can enforce. SQLite cannot prove that a stored digest
equals `blake2b(symbol.id)`, so exact derivation is established elsewhere: by the write
path (contract 1 binds the handle in the same statement as the symbol) and by schema
migration, which recomputes every persisted handle.
"""

import base64
import hashlib
import re

HANDLE_PREFIX = "s_"
HANDLE_DIGEST_CHARS = 22
HANDLE_LENGTH = len(HANDLE_PREFIX) + HANDLE_DIGEST_CHARS
# base64url. `-` stays last so it is a literal rather than a range in both a regular
# expression character class and a SQLite GLOB class.
HANDLE_ALPHABET = "A-Za-z0-9_-"
_HANDLE_PATTERN = re.compile(rf"^{HANDLE_PREFIX}[{HANDLE_ALPHABET}]{{{HANDLE_DIGEST_CHARS}}}$")


def handle_check_sql(column: str) -> str:
    """Return the SQL CHECK body enforcing the handle format on one column.

    Expresses exactly what `is_symbol_handle` accepts, insofar as SQLite can: fixed
    length, exact prefix, and no character outside `HANDLE_ALPHABET`.
    """
    return (
        f"length({column}) = {HANDLE_LENGTH}"
        f" AND substr({column}, 1, {len(HANDLE_PREFIX)}) = '{HANDLE_PREFIX}'"
        f" AND substr({column}, {len(HANDLE_PREFIX) + 1})"
        f" NOT GLOB '*[^{HANDLE_ALPHABET}]*'"
    )


def symbol_handle(symbol_id: str) -> str:
    """Derive the compact wire handle for one stable symbol ID.

    128-bit blake2b digest, base64url without padding: "s_" plus exactly 22
    characters. Deterministic across platforms and Python versions.
    """
    digest = hashlib.blake2b(symbol_id.encode("utf-8"), digest_size=16).digest()
    return HANDLE_PREFIX + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def is_symbol_handle(value: str) -> bool:
    """Whether a caller-supplied string has the compact handle shape."""
    return _HANDLE_PATTERN.fullmatch(value) is not None
