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
    ReferenceExtraction,
    ReferenceSyntax,
    call_usage_kinds,
    detect_language,
    file_scoped_container_types,
    is_call_usage,
    name_separator,
    query_dir,
    reference_extraction,
    reference_limitations,
    reference_syntax,
    reference_usage_kinds,
    to_treesitter_name,
    tree_sitter_language_names,
    uses_uppercase_constants,
)

__all__ = [
    "LANGUAGES",
    "GrammarNotInstalledError",
    "LanguagePackError",
    "LanguageSpec",
    "ReferenceExtraction",
    "ReferenceSyntax",
    "call_usage_kinds",
    "detect_language",
    "file_scoped_container_types",
    "get_installed_language",
    "install_grammars",
    "is_call_usage",
    "load_query",
    "missing_grammars",
    "name_separator",
    "query_dir",
    "reference_extraction",
    "reference_limitations",
    "reference_syntax",
    "reference_usage_kinds",
    "to_treesitter_name",
    "tree_sitter_language_names",
    "uses_uppercase_constants",
]
