"""Deterministic seed-symbol discovery and ranking for context queries."""

from dataclasses import dataclass
from enum import StrEnum

from synapse.core.context.keywords import QueryKeywords
from synapse.core.index import TOP_SYMBOL_KINDS, ReadProjections
from synapse.core.models import Symbol, SymbolKind

MAX_SEEDS = 5
MAX_ALTERNATES = 10
PER_TOKEN_CANDIDATE_LIMIT = 25

_KIND_RANKS: dict[str, int] = {str(kind): rank for rank, kind in enumerate(TOP_SYMBOL_KINDS)}
_UNRANKED_KIND = len(TOP_SYMBOL_KINDS)

_TEST_DIRECTORY_SEGMENTS = frozenset({"test", "tests", "testing", "__tests__", "spec", "specs"})


class SeedMatch(StrEnum):
    """How a seed candidate was matched to the question."""

    EXPLICIT = "explicit"
    EXACT_NAME = "exact-name"
    PREFIX = "prefix"
    TERM = "term"


_MATCH_TIERS: dict[SeedMatch, int] = {
    SeedMatch.EXPLICIT: 0,
    SeedMatch.EXACT_NAME: 1,
    SeedMatch.PREFIX: 2,
    SeedMatch.TERM: 3,
}


@dataclass(frozen=True, slots=True)
class Seed:
    """One ranked seed candidate with its match provenance."""

    symbol: Symbol
    match: SeedMatch
    matched_token: str


@dataclass(frozen=True, slots=True)
class SeedDiscovery:
    """The deterministic outcome of seed discovery, ambiguity included."""

    seeds: tuple[Seed, ...]
    alternates: tuple[Seed, ...]
    total_candidates: int
    unknown_symbol_ids: tuple[str, ...]


def kind_rank(kind: object) -> int:
    """Rank a symbol kind by overview relevance; unranked kinds sort last."""
    return _KIND_RANKS.get(str(kind), _UNRANKED_KIND)


def is_test_path(file_path: str) -> bool:
    """Report whether a path looks like test code (a ranking input, never confidence)."""
    parts = file_path.replace("\\", "/").split("/")
    if any(part.lower() in _TEST_DIRECTORY_SEGMENTS for part in parts[:-1]):
        return True
    file_name = parts[-1].lower()
    stem = file_name.split(".", 1)[0]
    return (
        stem.startswith("test_")
        or stem.endswith("_test")
        or stem.endswith("_tests")
        or ".spec." in file_name
        or ".test." in file_name
    )


def seed_rank_key(seed: Seed) -> tuple[int, int, int, int, str, int, str]:
    """Deterministic, int/str-only ordering key: lower ranks first."""
    symbol = seed.symbol
    return (
        _MATCH_TIERS[seed.match],
        1 if is_test_path(symbol.file_path) else 0,
        kind_rank(symbol.kind),
        len(symbol.name),
        symbol.file_path,
        symbol.start_line,
        symbol.id,
    )


@dataclass(slots=True)
class _Candidate:
    seed: Seed
    matched_tokens: set[str]


def _candidate_rank_key(candidate: _Candidate) -> tuple[int, int, int, int, int, str, int, str]:
    """Like seed_rank_key, but rewards symbols matched by more distinct query tokens."""
    base = seed_rank_key(candidate.seed)
    return (base[0], base[1], -len(candidate.matched_tokens), *base[2:])


def _merge_candidate(candidates: dict[str, _Candidate], seed: Seed) -> None:
    existing = candidates.get(seed.symbol.id)
    if existing is None:
        candidates[seed.symbol.id] = _Candidate(seed=seed, matched_tokens={seed.matched_token})
        return
    existing.matched_tokens.add(seed.matched_token)
    if seed_rank_key(seed) < seed_rank_key(existing.seed):
        existing.seed = seed


def _discover_explicit(
    reads: ReadProjections, explicit_ids: tuple[str, ...]
) -> tuple[list[Seed], tuple[str, ...]]:
    found = reads.get_symbols_by_ids(explicit_ids)
    seeds: list[Seed] = []
    unknown: list[str] = []
    for symbol_id in explicit_ids:
        symbol = found.get(symbol_id)
        if symbol is None:
            if symbol_id not in unknown:
                unknown.append(symbol_id)
        elif all(seed.symbol.id != symbol_id for seed in seeds):
            seeds.append(Seed(symbol=symbol, match=SeedMatch.EXPLICIT, matched_token=symbol_id))
    return seeds, tuple(unknown)


def _name_matches(symbol: Symbol, token: str) -> bool:
    lowered = token.lower()
    if lowered in symbol.name.lower():
        return True
    return symbol.qualified_name is not None and lowered in symbol.qualified_name.lower()


def _collect_token_candidates(
    reads: ReadProjections,
    tokens: tuple[str, ...],
    match: SeedMatch,
    candidates: dict[str, _Candidate],
) -> None:
    for token in tokens:
        if match is not SeedMatch.TERM:
            for symbol in reads.get_definition(token):
                if symbol.kind is SymbolKind.IMPORT:
                    continue
                _merge_candidate(
                    candidates,
                    Seed(symbol=symbol, match=SeedMatch.EXACT_NAME, matched_token=token),
                )
        prefix_matches, _ = reads.search_symbols_page(token, limit=PER_TOKEN_CANDIDATE_LIMIT)
        for symbol in prefix_matches:
            # Search also hits file paths; seeds keep name evidence only, and import
            # symbols are re-declarations of a name, not the entity the question means.
            if symbol.kind is SymbolKind.IMPORT or not _name_matches(symbol, token):
                continue
            exact = symbol.name == token or symbol.qualified_name == token
            seed_match = SeedMatch.EXACT_NAME if exact and match is not SeedMatch.TERM else match
            _merge_candidate(candidates, Seed(symbol=symbol, match=seed_match, matched_token=token))


def discover_seeds(
    reads: ReadProjections,
    keywords: QueryKeywords,
    explicit_ids: tuple[str, ...] = (),
) -> SeedDiscovery:
    """Resolve and rank seed symbols for a context query.

    Explicit symbol ids win outright; ids missing from the index are reported as
    unknown. Otherwise identifier-like tokens are matched by exact name first and
    FTS prefix second; plain terms are consulted only when identifiers match nothing.
    Ambiguity is explicit: candidates beyond the seed cap surface as alternates.
    """
    if explicit_ids:
        seeds, unknown = _discover_explicit(reads, explicit_ids)
        if seeds:
            return SeedDiscovery(
                seeds=tuple(seeds[:MAX_SEEDS]),
                alternates=tuple(seeds[MAX_SEEDS : MAX_SEEDS + MAX_ALTERNATES]),
                total_candidates=len(seeds),
                unknown_symbol_ids=unknown,
            )
    else:
        unknown = ()

    candidates: dict[str, _Candidate] = {}
    _collect_token_candidates(reads, keywords.identifiers, SeedMatch.PREFIX, candidates)
    if not candidates:
        _collect_token_candidates(reads, keywords.terms, SeedMatch.TERM, candidates)

    ranked = [candidate.seed for candidate in sorted(candidates.values(), key=_candidate_rank_key)]
    return SeedDiscovery(
        seeds=tuple(ranked[:MAX_SEEDS]),
        alternates=tuple(ranked[MAX_SEEDS : MAX_SEEDS + MAX_ALTERNATES]),
        total_candidates=len(ranked),
        unknown_symbol_ids=unknown,
    )
