"""Compatibility exports for explicit grammar installation commands."""

from synapse.core.grammar_install import (
    LanguagePackError,
    install_grammars,
    missing_grammars,
)

__all__ = ["LanguagePackError", "install_grammars", "missing_grammars"]
