"""Deterministic seed-symbol discovery and ranking for context queries."""

from dataclasses import dataclass
from enum import StrEnum

from synapse.core.context.keywords import QueryKeywords
from synapse.core.index import TOP_SYMBOL_KINDS, ReadProjections
from synapse.core.models import Symbol, SymbolKind

MAX_SEEDS = 5
MAX_ALTERNATES = 10
PER_TOKEN_CANDIDATE_LIMIT = 25
STRUCTURAL_CANDIDATE_LIMIT = 200
STRUCTURAL_MAX_PER_DIRECTORY = 2

_KIND_RANKS: dict[str, int] = {str(kind): rank for rank, kind in enumerate(TOP_SYMBOL_KINDS)}
_UNRANKED_KIND = len(TOP_SYMBOL_KINDS)

_TEST_DIRECTORY_SEGMENTS = frozenset({"test", "tests", "testing", "__tests__", "spec", "specs"})


class SeedMatch(StrEnum):
    """How a seed candidate was matched to the question."""

    EXPLICIT = "explicit"
    EXACT_NAME = "exact-name"
    PREFIX = "prefix"
    TERM = "term"
    STRUCTURAL = "structural"


class SeedOrigin(StrEnum):
    """Which discovery tier produced the returned seeds."""

    EXPLICIT_SYMBOL = "explicit-symbol"
    QUESTION_MATCH = "question-match"
    STRUCTURAL_FALLBACK = "structural-fallback"


_MATCH_TIERS: dict[SeedMatch, int] = {
    SeedMatch.EXPLICIT: 0,
    SeedMatch.EXACT_NAME: 1,
    SeedMatch.PREFIX: 2,
    SeedMatch.TERM: 3,
    SeedMatch.STRUCTURAL: 4,
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
    origin: SeedOrigin
    fallback_reason: str | None = None


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


def _token_candidates(
    reads: ReadProjections, token: str, match: SeedMatch
) -> tuple[list[Seed], list[Seed]]:
    """Return (exact, weaker) candidates for one token, deduplicated in rank order."""
    exact: list[Seed] = []
    exact_ids: set[str] = set()
    if match is not SeedMatch.TERM:
        for symbol in reads.get_definition(token):
            if symbol.kind is SymbolKind.IMPORT or symbol.id in exact_ids:
                continue
            exact.append(Seed(symbol=symbol, match=SeedMatch.EXACT_NAME, matched_token=token))
            exact_ids.add(symbol.id)
    weaker: list[Seed] = []
    prefix_matches, _ = reads.search_symbols_page(token, limit=PER_TOKEN_CANDIDATE_LIMIT)
    for symbol in prefix_matches:
        # Search also hits file paths; seeds keep name evidence only, and import
        # symbols are re-declarations of a name, not the entity the question means.
        if symbol.kind is SymbolKind.IMPORT or not _name_matches(symbol, token):
            continue
        is_exact = symbol.name == token or symbol.qualified_name == token
        if is_exact and match is not SeedMatch.TERM:
            if symbol.id not in exact_ids:
                exact.append(Seed(symbol=symbol, match=SeedMatch.EXACT_NAME, matched_token=token))
                exact_ids.add(symbol.id)
        else:
            weaker.append(Seed(symbol=symbol, match=match, matched_token=token))
    return exact, weaker


# Terms at least this long retry once with a shortened prefix when nothing matches,
# so morphological variants ("registration") still reach declarations ("register_...").
TERM_RELAXATION_MIN_LENGTH = 7
TERM_RELAXATION_PREFIX = 6


def _collect_token_candidates(
    reads: ReadProjections,
    tokens: tuple[str, ...],
    match: SeedMatch,
    candidates: dict[str, _Candidate],
    suppressed: list[Seed],
) -> None:
    """Merge per-token candidates, letting exact declarations dominate weaker matches.

    When a token has an exact production declaration, that token's prefix/term
    matches never become peer active seeds — they land in `suppressed` and stay
    visible as alternates. Exact test declarations stay active only when the token
    has no production exact match.
    """
    for token in tokens:
        exact, weaker = _token_candidates(reads, token, match)
        if match is SeedMatch.TERM and not weaker and len(token) >= TERM_RELAXATION_MIN_LENGTH:
            relaxed = token[:TERM_RELAXATION_PREFIX]
            _, weaker = _token_candidates(reads, relaxed, SeedMatch.TERM)
        for seed in exact:
            _merge_candidate(candidates, seed)
        has_production_exact = any(not is_test_path(seed.symbol.file_path) for seed in exact)
        if has_production_exact:
            suppressed.extend(weaker)
        else:
            # A test-only exact match must not hide production candidates.
            for seed in weaker:
                _merge_candidate(candidates, seed)


def _seed_directory(file_path: str) -> str:
    parts = file_path.replace("\\", "/").split("/")
    return "/".join(parts[:-1])


def _path_depth(file_path: str) -> int:
    return file_path.replace("\\", "/").count("/")


def _path_segments(file_path: str) -> list[str]:
    parts = file_path.replace("\\", "/").split("/")
    stem = parts[-1].rsplit(".", 1)[0]
    segments = parts[:-1]
    if stem and stem != "__init__":
        segments = [*segments, stem]
    return segments


def _import_segments(dotted_name: str) -> list[str]:
    normalized = dotted_name.replace("::", ".").replace("/", ".")
    return [segment for segment in normalized.split(".") if segment]


def _file_import_scores(import_counts: dict[str, int], file_paths: set[str]) -> dict[str, int]:
    """Score each file by distinct importing files whose declared import names it.

    An import matches a file when the file's path segments (extension stripped,
    `__init__` dropped) end with the import's dotted segments — declared module
    structure, never name-similarity guessing.
    """
    scores: dict[str, int] = {}
    for file_path in file_paths:
        segments = _path_segments(file_path)
        total = 0
        for dotted_name, count in import_counts.items():
            imported = _import_segments(dotted_name)
            if (
                imported
                and len(imported) <= len(segments)
                and segments[-len(imported) :] == imported
            ):
                total += count
        if total:
            scores[file_path] = total
    return scores


def _structural_seeds(reads: ReadProjections) -> list[Seed]:
    """Fallback seeds ranked by trustworthy structural evidence, diversified.

    Score inputs (all deterministic, all capped at 10 before weighting):
    3x incoming exact/scoped references (trusted usage centrality), 2x containment
    children (declares real structure), 4x file import reach (declared public
    surface), +2 public name (no leading underscore), +2 shallow production path.
    Heuristic unique-name references never contribute. Test paths and
    non-declaration kinds are excluded; a per-directory cap spreads seeds across
    repository areas; name order is only the last tiebreak.
    """
    trusted_in: dict[str, int] = {}
    pool: dict[str, Symbol] = {}
    for symbol, count in reads.trusted_incoming_degrees(STRUCTURAL_CANDIDATE_LIMIT):
        pool[symbol.id] = symbol
        trusted_in[symbol.id] = count
    child_counts = reads.containment_child_counts(1000)
    for symbol in reads.get_symbols_by_ids(sorted(child_counts)).values():
        pool.setdefault(symbol.id, symbol)
    for symbol in reads.top_declared_symbols(STRUCTURAL_CANDIDATE_LIMIT):
        pool.setdefault(symbol.id, symbol)

    eligible = [
        symbol
        for symbol in pool.values()
        if str(symbol.kind) in _KIND_RANKS and not is_test_path(symbol.file_path)
    ]
    public = [symbol for symbol in eligible if not symbol.name.startswith("_")]
    if len(public) >= MAX_SEEDS:
        # Private helpers orient nobody; keep them only when publics are scarce.
        eligible = public
    import_counts = reads.import_name_counts()
    file_scores = _file_import_scores(import_counts, {symbol.file_path for symbol in eligible})

    def score(symbol: Symbol) -> int:
        value = 3 * min(trusted_in.get(symbol.id, 0), 10)
        value += 2 * min(child_counts.get(symbol.id, 0), 10)
        value += 4 * min(file_scores.get(symbol.file_path, 0), 10)
        if not symbol.name.startswith("_"):
            value += 2
        if _path_depth(symbol.file_path) <= 3:
            value += 2
        return value

    ranked = sorted(
        eligible,
        key=lambda symbol: (
            -score(symbol),
            kind_rank(symbol.kind),
            _path_depth(symbol.file_path),
            symbol.file_path,
            symbol.start_line,
            symbol.id,
        ),
    )
    picked: list[Seed] = []
    per_directory: dict[str, int] = {}
    for symbol in ranked:
        directory = _seed_directory(symbol.file_path)
        if per_directory.get(directory, 0) >= STRUCTURAL_MAX_PER_DIRECTORY:
            continue
        per_directory[directory] = per_directory.get(directory, 0) + 1
        picked.append(Seed(symbol=symbol, match=SeedMatch.STRUCTURAL, matched_token=""))
        if len(picked) >= MAX_SEEDS:
            break
    return picked


def discover_seeds(
    reads: ReadProjections,
    keywords: QueryKeywords,
    explicit_ids: tuple[str, ...] = (),
) -> SeedDiscovery:
    """Resolve and rank seed symbols for a context query.

    Explicit symbol ids win outright; ids missing from the index are reported as
    unknown. Otherwise identifier-like tokens are matched by exact name first and
    FTS prefix second; plain terms are consulted only when identifiers match nothing.
    When the question matches no symbol at all, a bounded structural fallback seeds
    the query from connected production declarations and reports why. Ambiguity is
    explicit: candidates beyond the seed cap surface as alternates.
    """
    if explicit_ids:
        seeds, unknown = _discover_explicit(reads, explicit_ids)
        if seeds:
            return SeedDiscovery(
                seeds=tuple(seeds[:MAX_SEEDS]),
                alternates=tuple(seeds[MAX_SEEDS : MAX_SEEDS + MAX_ALTERNATES]),
                total_candidates=len(seeds),
                unknown_symbol_ids=unknown,
                origin=SeedOrigin.EXPLICIT_SYMBOL,
            )
    else:
        unknown = ()

    candidates: dict[str, _Candidate] = {}
    suppressed: list[Seed] = []
    _collect_token_candidates(reads, keywords.identifiers, SeedMatch.PREFIX, candidates, suppressed)
    if not candidates:
        suppressed = []
        _collect_token_candidates(reads, keywords.terms, SeedMatch.TERM, candidates, suppressed)

    if not candidates:
        structural = _structural_seeds(reads)
        had_tokens = bool(keywords.identifiers or keywords.terms)
        return SeedDiscovery(
            seeds=tuple(structural),
            alternates=(),
            total_candidates=len(structural),
            unknown_symbol_ids=unknown,
            origin=SeedOrigin.STRUCTURAL_FALLBACK,
            fallback_reason="no-question-match" if had_tokens else "no-question-tokens",
        )

    ranked = [candidate.seed for candidate in sorted(candidates.values(), key=_candidate_rank_key)]
    active_ids = {seed.symbol.id for seed in ranked[:MAX_SEEDS]}
    overflow = ranked[MAX_SEEDS:]
    suppressed_alternates = [
        seed for seed in sorted(suppressed, key=seed_rank_key) if seed.symbol.id not in active_ids
    ]
    alternates: list[Seed] = []
    alternate_ids: set[str] = set()
    for seed in [*overflow, *suppressed_alternates]:
        if seed.symbol.id in alternate_ids:
            continue
        alternates.append(seed)
        alternate_ids.add(seed.symbol.id)
        if len(alternates) >= MAX_ALTERNATES:
            break
    return SeedDiscovery(
        seeds=tuple(ranked[:MAX_SEEDS]),
        alternates=tuple(alternates),
        total_candidates=len(ranked) + len(suppressed_alternates),
        unknown_symbol_ids=unknown,
        origin=SeedOrigin.QUESTION_MATCH,
    )
