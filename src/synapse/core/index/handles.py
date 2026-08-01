"""Deterministic compact symbol handles derived from stable symbol IDs."""

import base64
import hashlib
import re

HANDLE_PREFIX = "s_"
_HANDLE_PATTERN = re.compile(r"^s_[A-Za-z0-9_-]{22}$")


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
