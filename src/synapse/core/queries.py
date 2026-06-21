"""Declarative tree-sitter query loading (the language-agnostic seam)."""

from pathlib import Path

from synapse.core.languages import query_dir

QUERY_ROOT = Path(__file__).resolve().parents[3] / "queries"


def load_query(language: str, name: str) -> str:
    """Return the tree-sitter query source for a language and query name."""
    query_path = QUERY_ROOT / query_dir(language) / f"{name}.scm"
    if not query_path.exists():
        msg = f"Missing query file for language={language!r}, name={name!r}: {query_path}"
        raise FileNotFoundError(msg)
    query_text = query_path.read_text(encoding="utf-8").strip()
    if not query_text:
        msg = f"Empty query file for language={language!r}, name={name!r}: {query_path}"
        raise ValueError(msg)
    return query_text
