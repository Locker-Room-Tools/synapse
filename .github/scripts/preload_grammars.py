"""Preload every grammar used by Synapse into the language-pack cache."""

from tree_sitter_language_pack import download, get_language

from synapse.core.languages import LANGUAGES, to_treesitter_name


def main() -> None:
    """Download and validate all configured tree-sitter grammars sequentially."""
    grammar_names = sorted({to_treesitter_name(language) for language in LANGUAGES})
    download(grammar_names)
    for grammar_name in grammar_names:
        get_language(grammar_name)
    print(f"Prepared {len(grammar_names)} tree-sitter grammars")


if __name__ == "__main__":
    main()
