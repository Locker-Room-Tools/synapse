"""Language seam: supported-language registry, grammars, and tree-sitter queries."""

from synapse.core.languages.grammar_install import (
    LanguagePackError,
    install_grammars,
    missing_grammars,
)
from synapse.core.languages.grammars import GrammarNotInstalledError, get_installed_language
from synapse.core.languages.queries import load_query
from synapse.core.languages.registry import (
    LANGUAGES,
    LanguageSpec,
    detect_language,
    name_separator,
    query_dir,
    to_treesitter_name,
    tree_sitter_language_names,
    uses_uppercase_constants,
)

__all__ = [
    "LANGUAGES",
    "GrammarNotInstalledError",
    "LanguagePackError",
    "LanguageSpec",
    "detect_language",
    "get_installed_language",
    "install_grammars",
    "load_query",
    "missing_grammars",
    "name_separator",
    "query_dir",
    "to_treesitter_name",
    "tree_sitter_language_names",
    "uses_uppercase_constants",
]
