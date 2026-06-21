"""Supported language registry and tree-sitter name helpers."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LanguageSpec:
    """Static metadata for one supported language."""

    id: str
    tree_sitter_name: str
    extensions: tuple[str, ...]
    query_dir: str


LANGUAGES: dict[str, LanguageSpec] = {
    "c": LanguageSpec(
        id="c",
        tree_sitter_name="c",
        extensions=(".c", ".h"),
        query_dir="c",
    ),
    "cpp": LanguageSpec(
        id="cpp",
        tree_sitter_name="cpp",
        extensions=(".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"),
        query_dir="cpp",
    ),
    "csharp": LanguageSpec(
        id="csharp",
        tree_sitter_name="csharp",
        extensions=(".cs",),
        query_dir="c_sharp",
    ),
    "dart": LanguageSpec(
        id="dart",
        tree_sitter_name="dart",
        extensions=(".dart",),
        query_dir="dart",
    ),
    "go": LanguageSpec(
        id="go",
        tree_sitter_name="go",
        extensions=(".go",),
        query_dir="go",
    ),
    "java": LanguageSpec(
        id="java",
        tree_sitter_name="java",
        extensions=(".java",),
        query_dir="java",
    ),
    "javascript": LanguageSpec(
        id="javascript",
        tree_sitter_name="javascript",
        extensions=(".js", ".jsx", ".mjs", ".cjs"),
        query_dir="javascript",
    ),
    "kotlin": LanguageSpec(
        id="kotlin",
        tree_sitter_name="kotlin",
        extensions=(".kt", ".kts"),
        query_dir="kotlin",
    ),
    "lua": LanguageSpec(
        id="lua",
        tree_sitter_name="lua",
        extensions=(".lua",),
        query_dir="lua",
    ),
    "php": LanguageSpec(
        id="php",
        tree_sitter_name="php",
        extensions=(".php",),
        query_dir="php",
    ),
    "python": LanguageSpec(
        id="python",
        tree_sitter_name="python",
        extensions=(".py",),
        query_dir="python",
    ),
    "ruby": LanguageSpec(
        id="ruby",
        tree_sitter_name="ruby",
        extensions=(".rb",),
        query_dir="ruby",
    ),
    "rust": LanguageSpec(
        id="rust",
        tree_sitter_name="rust",
        extensions=(".rs",),
        query_dir="rust",
    ),
    "scala": LanguageSpec(
        id="scala",
        tree_sitter_name="scala",
        extensions=(".scala", ".sc"),
        query_dir="scala",
    ),
    "swift": LanguageSpec(
        id="swift",
        tree_sitter_name="swift",
        extensions=(".swift",),
        query_dir="swift",
    ),
    "typescript": LanguageSpec(
        id="typescript",
        tree_sitter_name="typescript",
        extensions=(".ts", ".mts", ".cts"),
        query_dir="typescript",
    ),
    "tsx": LanguageSpec(
        id="tsx",
        tree_sitter_name="tsx",
        extensions=(".tsx",),
        query_dir="typescript",
    ),
}

_EXTENSION_TO_LANGUAGE = {
    extension: language.id for language in LANGUAGES.values() for extension in language.extensions
}


def detect_language(path: Path) -> str | None:
    """Return the normalized language id for a file path when supported."""
    return _EXTENSION_TO_LANGUAGE.get(path.suffix.lower())


def to_treesitter_name(language: str) -> str:
    """Return the tree-sitter language name for a normalized language id."""
    try:
        return LANGUAGES[language].tree_sitter_name
    except KeyError as exc:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg) from exc


def query_dir(language: str) -> str:
    """Return the query directory name for a normalized language id."""
    try:
        return LANGUAGES[language].query_dir
    except KeyError as exc:
        msg = f"Unsupported language: {language}"
        raise ValueError(msg) from exc
