"""Offline-only access to installed tree-sitter grammars."""

from functools import cache

from tree_sitter import Language
from tree_sitter_language_pack import downloaded_languages, get_language


class GrammarNotInstalledError(RuntimeError):
    """Raised when parsing requires a grammar that is not available locally."""


@cache
def get_installed_language(name: str) -> Language:
    """Load a cached grammar without allowing an implicit network download."""
    if name not in frozenset(downloaded_languages()):
        msg = (
            f"Tree-sitter grammar {name!r} is not installed. "
            "Run 'synapse grammars install' before indexing."
        )
        raise GrammarNotInstalledError(msg)
    return get_language(name)
