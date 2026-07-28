"""Explicit installation of supported tree-sitter grammars."""

from tree_sitter_language_pack import Error as LanguagePackError
from tree_sitter_language_pack import downloaded_languages, prefetch

from synapse.core.languages.grammars import get_installed_language
from synapse.core.languages.registry import tree_sitter_language_names


def missing_grammars() -> tuple[str, ...]:
    """Return supported grammar names absent from the local parser cache."""
    installed = frozenset(downloaded_languages())
    return tuple(name for name in tree_sitter_language_names() if name not in installed)


def install_grammars() -> tuple[str, ...]:
    """Download and validate every grammar supported by Synapse."""
    grammar_names = tree_sitter_language_names()
    prefetch(list(grammar_names))
    get_installed_language.cache_clear()
    return grammar_names


__all__ = ["LanguagePackError", "install_grammars", "missing_grammars"]
