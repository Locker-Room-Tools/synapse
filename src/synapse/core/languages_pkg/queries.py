"""Declarative tree-sitter query loading (the language-agnostic seam)."""

from importlib import resources

from synapse.core.languages import query_dir

QUERY_ROOT = resources.files("synapse") / "queries"


def load_query(language: str, name: str) -> str:
    """Return the tree-sitter query source for a language and query name."""
    query_path = QUERY_ROOT / query_dir(language) / f"{name}.scm"
    if not query_path.is_file():
        msg = f"Missing query file for language={language!r}, name={name!r}: {query_path}"
        raise FileNotFoundError(msg)
    query_text = query_path.read_text(encoding="utf-8").strip()
    if not query_text:
        msg = f"Empty query file for language={language!r}, name={name!r}: {query_path}"
        raise ValueError(msg)
    return query_text
