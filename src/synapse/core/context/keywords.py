"""Deterministic keyword extraction from a natural-language context question."""

import re
from dataclasses import dataclass

STOPWORDS: frozenset[str] = frozenset(
    {
        "about", "after", "all", "and", "answer", "any", "are", "area", "around",
        "back", "because", "been", "before", "being", "between", "both", "but",
        "call", "called", "calling", "calls", "can", "class", "code", "codebase",
        "come", "could", "current", "defined", "definition", "describe", "did",
        "does", "doing", "done", "down", "during", "each", "end", "every",
        "explain", "file", "files", "find", "flow", "for", "from", "function",
        "get", "gets", "give", "goes", "happen", "happens", "have", "how",
        "implementation", "implemented", "inside", "into", "its", "logic",
        "main", "make", "makes", "method", "module", "need", "new", "not",
        "one", "other", "our", "out", "over", "part", "path", "place", "please",
        "point", "project", "repo", "repository", "run", "runs", "set", "should",
        "show", "some", "source", "start", "symbol", "take", "than", "that",
        "the", "their", "them", "then", "there", "these", "they", "this",
        "those", "through", "trace", "under", "until", "use", "used", "uses",
        "using", "very", "walk", "want", "was", "way", "were", "what", "when",
        "where", "which", "while", "who", "why", "will", "with", "work",
        "working", "works", "would", "you", "your",
    }
)  # fmt: skip

MAX_TOKEN_LENGTH = 128

_QUOTED_PATTERN = re.compile(r"[`'\"]([^`'\"]+)[`'\"]")
_TOKEN_PATTERN = re.compile(r"[\w.]+")
_CASE_TRANSITION_PATTERN = re.compile(r"[a-z][A-Z]")
# ASCII camel/acronym splitting plus whole runs of non-ASCII word characters, so
# non-English question words survive tokenization instead of being discarded.
_SUBTOKEN_PATTERN = re.compile(r"[A-Z]+(?![a-z])|[A-Za-z][a-z0-9]*|[0-9]+|[^\W\da-zA-Z_]+")


@dataclass(frozen=True, slots=True)
class QueryKeywords:
    """Ordered, deduplicated tokens extracted from one question."""

    identifiers: tuple[str, ...]
    terms: tuple[str, ...]


def _is_identifier_like(token: str, quoted: frozenset[str]) -> bool:
    if token in quoted:
        return True
    if "_" in token or "." in token:
        return True
    return _CASE_TRANSITION_PATTERN.search(token) is not None


def _subtokens(token: str) -> list[str]:
    return [match.group(0) for match in _SUBTOKEN_PATTERN.finditer(token)]


def _keep_term(subtoken: str) -> bool:
    lowered = subtoken.lower()
    if lowered in STOPWORDS:
        return False
    if len(subtoken) >= 3:
        return True
    return len(subtoken) == 2 and subtoken.isupper()


def _add_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def extract_keywords(question: str) -> QueryKeywords:
    """Extract identifier-like tokens and lowercased search terms, in question order.

    Deterministic: quoted or backticked spans are always identifiers; a bare token is
    identifier-like when it contains ``_``, an interior dot, or a camelCase transition.
    Terms are the lowercased sub-tokens (camelCase/snake_case/dotted splits) minus
    stopwords and short fragments.
    """
    quoted = frozenset(
        stripped
        for match in _QUOTED_PATTERN.finditer(question)
        if (stripped := match.group(1).strip())
    )
    identifiers: list[str] = []
    terms: list[str] = []
    for match in _TOKEN_PATTERN.finditer(question):
        token = match.group(0).strip("._")
        if not token or len(token) > MAX_TOKEN_LENGTH:
            # No real identifier is this long; over-long tokens would only feed
            # pathological search patterns downstream.
            continue
        if _is_identifier_like(token, quoted):
            _add_unique(identifiers, token)
        for subtoken in _subtokens(token):
            if _keep_term(subtoken):
                _add_unique(terms, subtoken.lower())
    return QueryKeywords(identifiers=tuple(identifiers), terms=tuple(terms))
