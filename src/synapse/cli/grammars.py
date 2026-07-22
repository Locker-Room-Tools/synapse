"""Explicit tree-sitter grammar installation commands."""

from tree_sitter_language_pack import Error as LanguagePackError
from tree_sitter_language_pack import prefetch

from synapse.core.languages import tree_sitter_language_names


def install_grammars() -> tuple[str, ...]:
    """Download and validate every grammar supported by Synapse."""
    grammar_names = tree_sitter_language_names()
    prefetch(list(grammar_names))
    return grammar_names


__all__ = ["LanguagePackError", "install_grammars"]
