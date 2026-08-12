"""Literal term-to-declaration matching primitives for orientation."""

from enum import StrEnum

from synapse.core.index import kind_rank
from synapse.core.models import Symbol, SymbolKind

GENERIC_TERM_MATCH_FLOOR = 25
GENERIC_TERM_MATCH_RATIO = 100

_CALLABLE_VALUE_KINDS = frozenset({SymbolKind.VARIABLE, SymbolKind.CONSTANT})


class TermMatch(StrEnum):
    """How a supplied repository term reached a declaration or file."""

    EXACT = "exact"
    PREFIX = "prefix"
    SUBSTRING = "substring"
    PATH = "path"
    MAP = "map"


_MATCH_TIERS: dict[TermMatch, int] = {
    TermMatch.EXACT: 0,
    TermMatch.PREFIX: 1,
    TermMatch.SUBSTRING: 2,
    TermMatch.PATH: 3,
    TermMatch.MAP: 4,
}


def match_tier(match: TermMatch) -> int:
    """Rank a match provenance for ordering: strongest evidence first."""
    return _MATCH_TIERS[match]


def name_matches(symbol: Symbol, term: str) -> bool:
    """Whether the term appears in the declaration's own or qualified name."""
    lowered = term.lower()
    if lowered in symbol.name.lower():
        return True
    return symbol.qualified_name is not None and lowered in symbol.qualified_name.lower()


def prefix_at_word_start(name: str, prefix: str) -> bool:
    """Report whether ``prefix`` starts a word of ``name`` (snake or camel)."""
    lowered = name.lower()
    if lowered.startswith(prefix):
        return True
    if f"_{prefix}" in lowered:
        return True
    camel = prefix[:1].upper() + prefix[1:]
    index = name.find(camel)
    while index != -1:
        if index > 0 and name[index - 1].islower():
            return True
        index = name.find(camel, index + 1)
    return False


def generic_limit(symbol_count: int) -> int:
    """Crowd threshold above which a term stops discriminating declarations."""
    return max(GENERIC_TERM_MATCH_FLOOR, symbol_count // GENERIC_TERM_MATCH_RATIO)


def effective_kind_rank(symbol: Symbol) -> int:
    """Kind rank that recognizes parser-proven callable runtime values.

    A `variable`/`constant` declaration carrying a stored signature is a callable
    in kind-rank terms — arrow functions and function values must not lose to
    unrelated nested types merely because of their storage kind.
    """
    if symbol.kind in _CALLABLE_VALUE_KINDS and symbol.signature:
        return kind_rank(SymbolKind.FUNCTION)
    return kind_rank(symbol.kind)
