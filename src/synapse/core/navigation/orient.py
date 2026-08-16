"""Ranked, production-first orientation over agent-supplied repository terms."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from synapse.core.index import (
    TOP_SYMBOL_KINDS,
    ReadProjections,
    RepoMap,
    SymbolIndex,
    compute_repo_map,
    is_generated_path,
    is_test_path,
    load_repo_map,
    symbol_handle,
)
from synapse.core.models import Symbol, SymbolKind
from synapse.core.navigation.budget import (
    ORIENT_DEFAULT_TOKEN_BUDGET,
    ORIENT_MAX_TOKEN_BUDGET,
    ORIENT_MIN_TOKEN_BUDGET,
    DropStep,
    clamp,
    enforce_budget,
)
from synapse.core.navigation.matching import (
    TermMatch,
    effective_kind_rank,
    generic_limit,
    match_tier,
    name_matches,
    prefix_at_word_start,
    subtoken_match,
)
from synapse.core.navigation.render import FileTable

MAX_TERMS = 12
MAX_MATCHES = 12
MAX_WEAK = 8
MIN_MATCHES_BEFORE_HARD_CAP = 3
SEARCH_PAGE_LIMIT = 25
# Crowded terms retrieve a larger internal page so whole-subtoken matches beyond
# the ordinary page can still be accepted; matches the reads-layer MAX_PAGE_LIMIT.
CROWDED_PAGE_LIMIT = 200
PATH_MATCH_LIMIT = 20
MAX_FILE_MATCHES = 8
CENTRALITY_BUCKET_CAP = 10
MAP_ANCHORS_PER_AREA = 2
MAP_BRIDGES = 4
MAP_BRIDGE_EXAMPLES = 1
_RARE_SENTINEL = 10**9


@dataclass(frozen=True, slots=True)
class OrientRequest:
    """One orientation request: literal repository terms, never natural language."""

    terms: tuple[str, ...] = ()
    path_scope: str | None = None
    token_budget: int = ORIENT_DEFAULT_TOKEN_BUDGET


@dataclass(slots=True)
class _Candidate:
    symbol: Symbol
    match: TermMatch
    name_terms: set[str] = field(default_factory=set)
    path_terms: set[str] = field(default_factory=set)
    trusted_in: int = 0
    entrypoint: bool = False


def _merge(
    candidates: dict[str, _Candidate],
    symbol: Symbol,
    match: TermMatch,
    term: str,
    *,
    via_path: bool,
) -> None:
    candidate = candidates.get(symbol.id)
    if candidate is None:
        candidate = _Candidate(symbol=symbol, match=match)
        candidates[symbol.id] = candidate
    elif match_tier(match) < match_tier(candidate.match):
        candidate.match = match
    if via_path:
        candidate.path_terms.add(term)
    else:
        candidate.name_terms.add(term)


@dataclass(slots=True)
class _FileMatch:
    """A file whose path matched a term literally, with or without declarations."""

    path: str
    term: str
    declarations: int


def _declarations_in(reads: ReadProjections, file_path: str) -> list[Symbol]:
    return [
        symbol
        for symbol in reads.list_symbols_for_file(file_path)
        if symbol.kind is not SymbolKind.IMPORT
    ]


def _best_declaration(declared: list[Symbol]) -> Symbol | None:
    if not declared:
        return None
    ranked = [symbol for symbol in declared if symbol.kind in TOP_SYMBOL_KINDS] or declared
    return min(ranked, key=lambda symbol: (effective_kind_rank(symbol), symbol.start_line))


def _normalized_scope(path_scope: str | None) -> str | None:
    if path_scope is None:
        return None
    scope = path_scope.replace("\\", "/").strip("/")
    scope = scope.removeprefix("./")
    return scope or None


def _in_scope(file_path: str, scope: str | None) -> bool:
    if scope is None:
        return True
    return file_path == scope or file_path.startswith(f"{scope}/")


@dataclass(slots=True)
class _MapBridgeRef:
    """A trusted cross-area link, addressed by area path until indexes are known."""

    from_area: str
    to_area: str
    references: int
    imports: int
    examples: list[str]


@dataclass(slots=True)
class _OrientState:
    matches: list[_Candidate]
    weak: list[_Candidate]
    crowded: dict[str, int]
    crowded_collapsed: bool
    unmatched: list[str]
    file_matches: list[_FileMatch]
    files_discovered: int
    path_capped: bool
    name_omitted: int
    map_areas: list[dict[str, object]]
    map_entrypoints: list[dict[str, object]]
    map_bridges: list[_MapBridgeRef]
    discovered: int


def _candidate_entry(
    candidate: _Candidate,
    files: FileTable,
    term_totals: dict[str, int],
) -> dict[str, object]:
    symbol = candidate.symbol
    entry: dict[str, object] = {
        "h": symbol_handle(symbol.id),
        "n": symbol.name,
        "k": str(symbol.kind),
        "f": files.index(symbol.file_path),
        "l": symbol.start_line,
        "m": str(candidate.match),
    }
    matched = sorted(
        candidate.name_terms | candidate.path_terms,
        key=lambda term: (term_totals.get(term, _RARE_SENTINEL), term),
    )
    if matched:
        entry["t"] = matched[0]
    if candidate.trusted_in > 0:
        entry["in"] = min(candidate.trusted_in, CENTRALITY_BUCKET_CAP)
    if candidate.entrypoint:
        entry["ep"] = True
    return entry


def _rank_key(
    candidate: _Candidate,
    term_totals: dict[str, int],
) -> tuple[int, int, int, int, int, int, int, int, int, str, int, str]:
    symbol = candidate.symbol
    rarest = min(
        (term_totals.get(term, _RARE_SENTINEL) for term in candidate.name_terms),
        default=_RARE_SENTINEL,
    )
    # Production-first, then evidence volume before evidence strength: a candidate
    # matching several of the supplied terms outranks one matching a single term
    # exactly — corroboration across the agent's vocabulary discriminates better
    # than the tier of any one match. Tier still decides between equal term counts.
    return (
        1 if is_test_path(symbol.file_path) else 0,
        1 if is_generated_path(symbol.file_path) else 0,
        -len(candidate.name_terms | candidate.path_terms),
        match_tier(candidate.match),
        rarest,
        -min(candidate.trusted_in, CENTRALITY_BUCKET_CAP),
        0 if candidate.entrypoint else 1,
        effective_kind_rank(symbol),
        len(symbol.name),
        symbol.file_path,
        symbol.start_line,
        symbol.id,
    )


@dataclass(slots=True)
class _TermResults:
    candidates: dict[str, _Candidate]
    term_totals: dict[str, int]
    crowded: dict[str, int]
    unmatched: list[str]
    file_matches: list[_FileMatch]
    name_omitted: int
    # Distinct files matching any path term, counted in SQL: per-term totals overlap,
    # so only a union gives an omission count that is exactly matched minus returned.
    files_discovered: int
    # A term's path matches outgrew PATH_MATCH_LIMIT, so some were never retrieved.
    path_capped: bool


def _match_all_terms(
    reads: ReadProjections,
    terms: tuple[str, ...],
    declaration_count: int,
    scope: str | None,
) -> _TermResults:
    candidates: dict[str, _Candidate] = {}
    term_totals: dict[str, int] = {}
    crowded: dict[str, int] = {}
    unmatched: list[str] = []
    file_matches: list[_FileMatch] = []
    matched_paths: set[str] = set()
    path_terms: list[str] = []
    path_capped = False
    name_omitted = 0
    # Measured against the searchable declarations of this scope, computed once, so both
    # sides of the crowd comparison describe the same population.
    limit = generic_limit(declaration_count)
    seen_terms: set[str] = set()
    for term in terms:
        cleaned = term.strip()
        if not cleaned or cleaned in seen_terms:
            continue
        seen_terms.add(cleaned)
        # Counted over the same declaration-only, scoped space the page retrieves from,
        # so crowding compares like with like: a term that is generic workspace-wide but
        # rare inside the scope is not demoted, and imports never inflate the count.
        total = reads.symbol_name_match_count(cleaned, path_scope=scope, declarations_only=True)
        term_totals[cleaned] = total
        generic = total > limit
        if generic:
            crowded[cleaned] = total
        matched_any = False
        for symbol in reads.get_definition(cleaned):
            if symbol.kind is SymbolKind.IMPORT or not _in_scope(symbol.file_path, scope):
                continue
            _merge(candidates, symbol, TermMatch.EXACT, cleaned, via_path=False)
            matched_any = True
        # Every narrowing binds inside the query, before the page bound. The name channel
        # excludes file paths so path-only rows cannot fill the page; the scope and the
        # import exclusion are there for the same reason — filtering a global page
        # afterwards lets out-of-scope rows or import statements consume it and hide a
        # real in-scope declaration.
        page_symbols, _ = reads.search_symbol_names_page(
            cleaned,
            path_scope=scope,
            declarations_only=True,
            limit=CROWDED_PAGE_LIMIT if generic else SEARCH_PAGE_LIMIT,
        )
        name_omitted += max(0, total - len(page_symbols))
        for symbol in page_symbols:
            if symbol.kind is SymbolKind.IMPORT or not name_matches(symbol, cleaned):
                continue
            if not _in_scope(symbol.file_path, scope):
                continue
            if symbol.name == cleaned or symbol.qualified_name == cleaned:
                match = TermMatch.EXACT
            elif prefix_at_word_start(symbol.name, cleaned.lower()):
                match = TermMatch.PREFIX
            else:
                match = TermMatch.SUBSTRING
            if (
                generic
                and match is not TermMatch.EXACT
                and not subtoken_match(symbol.name, cleaned)
            ):
                # A crowded term keeps its exact hits and whole-subtoken hits; bare
                # mid-word substring hits would only flood the ranking, so the term
                # is still reported in crowded_terms.
                matched_any = True
                continue
            _merge(candidates, symbol, match, cleaned, via_path=False)
            matched_any = True
        # A term is treated as a literal path only when shaped like one; a bare word
        # still matches whole path components. That rule now lives in the projection, so
        # what is retrieved, what is counted, and what is accepted are the same set.
        matched_files, path_page = reads.files_matching_path(
            cleaned, path_scope=scope, limit=PATH_MATCH_LIMIT
        )
        path_terms.append(cleaned)
        if int(cast(int, path_page["total"])) > len(matched_files):
            path_capped = True
        for file_path in matched_files:
            if not _in_scope(file_path, scope):
                continue
            matched_any = True
            declared = _declarations_in(reads, file_path)
            if file_path not in matched_paths:
                matched_paths.add(file_path)
                # Emitted even with zero declarations: that is exactly the match an
                # earlier revision dropped, leaving the term "matched" with nothing
                # in the payload to show for it.
                file_matches.append(
                    _FileMatch(path=file_path, term=cleaned, declarations=len(declared))
                )
            declaration = _best_declaration(declared)
            if declaration is not None:
                _merge(candidates, declaration, TermMatch.PATH, cleaned, via_path=True)
        if not matched_any:
            unmatched.append(cleaned)
    file_matches.sort(
        key=lambda match: (
            1 if is_test_path(match.path) else 0,
            1 if is_generated_path(match.path) else 0,
            match.path,
        )
    )
    return _TermResults(
        candidates=candidates,
        term_totals=term_totals,
        crowded=crowded,
        unmatched=unmatched,
        file_matches=file_matches,
        name_omitted=name_omitted,
        files_discovered=reads.count_files_matching_paths(path_terms, path_scope=scope),
        path_capped=path_capped,
    )


@dataclass(frozen=True, slots=True)
class _MapAnchorRef:
    symbol_id: str
    name: str
    kind: str
    file_path: str
    start_line: int
    trusted_in: int


def _map_orientation(
    repo_map: RepoMap,
    scope: str | None,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[_MapAnchorRef],
    list[_MapBridgeRef],
]:
    areas: list[dict[str, object]] = []
    anchors: list[_MapAnchorRef] = []
    for area in repo_map.areas:
        if scope is not None and not (
            area.path == scope or area.path.startswith(f"{scope}/") or scope.startswith(area.path)
        ):
            continue
        entry: dict[str, object] = {"p": area.path, "files": area.files, "symbols": area.symbols}
        if area.is_tests:
            entry["tests"] = True
        areas.append(entry)
        for anchor in area.anchors[:MAP_ANCHORS_PER_AREA]:
            anchors.append(
                _MapAnchorRef(
                    symbol_id=anchor.symbol_id,
                    name=anchor.name,
                    kind=anchor.kind,
                    file_path=anchor.file_path,
                    start_line=anchor.start_line,
                    trusted_in=anchor.trusted_in,
                )
            )
    entrypoints: list[dict[str, object]] = []
    for entrypoint in repo_map.entrypoints:
        if not _in_scope(entrypoint.file_path, scope):
            continue
        entry = {"n": entrypoint.name, "p": entrypoint.file_path, "s": entrypoint.signal}
        if entrypoint.symbol_id:
            entry["h"] = symbol_handle(entrypoint.symbol_id)
            entry["l"] = entrypoint.start_line
        entrypoints.append(entry)
    # Bridges carry only the areas actually projected, so every reference resolves
    # inside this payload. The underlying evidence is already trust-filtered:
    # cross-area reference counts come from exact/scoped relations only.
    area_paths = {str(area["p"]) for area in areas}
    bridges = [
        _MapBridgeRef(
            from_area=bridge.from_area,
            to_area=bridge.to_area,
            references=bridge.references,
            imports=bridge.imports,
            examples=list(bridge.examples[:MAP_BRIDGE_EXAMPLES]),
        )
        for bridge in repo_map.bridges
        if bridge.from_area in area_paths and bridge.to_area in area_paths
    ][:MAP_BRIDGES]
    return areas, entrypoints, anchors, bridges


def orient_workspace(
    index: SymbolIndex,
    request: OrientRequest,
    *,
    workspace_root: Path,
) -> str:
    """Rank a bounded, production-first orientation set for literal repository terms.

    Deterministic for one index state. Empty ``terms`` explicitly requests
    repository-map orientation. The result is a compact JSON string whose size
    never exceeds ``token_budget * 4`` characters.
    """
    if len(request.terms) > MAX_TERMS:
        msg = f"synapse_orient accepts at most {MAX_TERMS} terms, got {len(request.terms)}"
        raise ValueError(msg)
    token_budget = clamp(request.token_budget, ORIENT_MIN_TOKEN_BUDGET, ORIENT_MAX_TOKEN_BUDGET)
    scope = _normalized_scope(request.path_scope)

    with index.read_session() as reads:
        stats = reads.workspace_stats()
        symbol_count = cast(int, stats["symbols"])
        repo_map = load_repo_map(reads) or compute_repo_map(reads)
        if request.terms:
            results = _match_all_terms(
                reads, request.terms, reads.declaration_count(path_scope=scope), scope
            )
            candidates = results.candidates
            term_totals = results.term_totals
            crowded = results.crowded
            unmatched = results.unmatched
            file_matches = results.file_matches
            files_discovered = results.files_discovered
            path_capped = results.path_capped
            name_omitted = results.name_omitted
            map_areas: list[dict[str, object]] = []
            map_entrypoints: list[dict[str, object]] = []
            map_bridges: list[_MapBridgeRef] = []
        else:
            candidates = {}
            term_totals = {}
            crowded = {}
            unmatched = []
            file_matches = []
            files_discovered = 0
            path_capped = False
            name_omitted = 0
            map_areas, map_entrypoints, anchors, map_bridges = _map_orientation(repo_map, scope)
            anchor_symbols = reads.get_symbols_by_ids(
                sorted({anchor.symbol_id for anchor in anchors})
            )
            for anchor in anchors:
                symbol = anchor_symbols.get(anchor.symbol_id)
                if symbol is None:
                    continue
                candidate = _Candidate(symbol=symbol, match=TermMatch.MAP)
                candidate.trusted_in = anchor.trusted_in
                candidates[symbol.id] = candidate
        if candidates:
            trusted = reads.trusted_incoming_degrees_for_ids(sorted(candidates))
            entry_ids = {e.symbol_id for e in repo_map.entrypoints if e.symbol_id}
            anchor_ids = {a.symbol_id for area in repo_map.areas for a in area.anchors}
            for symbol_id, candidate in candidates.items():
                candidate.trusted_in = max(candidate.trusted_in, trusted.get(symbol_id, 0))
                candidate.entrypoint = symbol_id in entry_ids or symbol_id in anchor_ids

    ranked = sorted(candidates.values(), key=lambda c: _rank_key(c, term_totals))
    state = _OrientState(
        matches=ranked[:MAX_MATCHES],
        weak=ranked[MAX_MATCHES : MAX_MATCHES + MAX_WEAK],
        crowded=crowded,
        crowded_collapsed=False,
        unmatched=unmatched,
        file_matches=file_matches[:MAX_FILE_MATCHES],
        files_discovered=files_discovered,
        path_capped=path_capped,
        name_omitted=name_omitted,
        map_areas=map_areas,
        map_entrypoints=map_entrypoints,
        map_bridges=map_bridges,
        discovered=len(ranked),
    )

    language_entries = cast(list[dict[str, object]], stats["languages"])
    language_names = [str(entry["language"]) for entry in language_entries]

    def assemble(meta: dict[str, object]) -> dict[str, object]:
        files = FileTable()
        match_entries = [_candidate_entry(c, files, term_totals) for c in state.matches]
        weak_entries = [_candidate_entry(c, files, term_totals) for c in state.weak]
        # Every file-table row must be reachable from a payload entry, so a matched
        # path is registered only together with the entry that references it.
        file_entries = [
            {"f": files.index(match.path), "t": match.term, "d": match.declarations}
            for match in state.file_matches
        ]
        payload: dict[str, object] = {"files": files.paths(), "matches": match_entries}
        if weak_entries:
            payload["weak"] = weak_entries
        if file_entries:
            payload["file_matches"] = file_entries
        if state.crowded_collapsed:
            payload["crowded_terms_omitted"] = len(state.crowded)
        elif state.crowded:
            payload["crowded_terms"] = dict(state.crowded)
        if state.unmatched:
            payload["unmatched_terms"] = list(state.unmatched)
        if state.map_areas or state.map_entrypoints:
            area_index = {str(area["p"]): position for position, area in enumerate(state.map_areas)}
            map_payload: dict[str, object] = {
                "areas": state.map_areas,
                "entrypoints": state.map_entrypoints,
            }
            bridge_entries: list[dict[str, object]] = []
            for bridge in state.map_bridges:
                source = area_index.get(bridge.from_area)
                target = area_index.get(bridge.to_area)
                if source is None or target is None:
                    continue
                bridge_entry: dict[str, object] = {"a": source, "b": target}
                if bridge.references:
                    bridge_entry["r"] = bridge.references
                if bridge.imports:
                    bridge_entry["i"] = bridge.imports
                if bridge.examples:
                    bridge_entry["x"] = list(bridge.examples)
                bridge_entries.append(bridge_entry)
            if bridge_entries:
                map_payload["bridges"] = bridge_entries
            payload["map"] = map_payload
        returned = len(state.matches) + len(state.weak)
        coverage: dict[str, object] = {
            "scope": "ranked-orientation",
            "discovered": state.discovered,
            "returned": returned,
            "omitted": max(0, state.discovered - returned),
            "index": {"files": stats["files"], "symbols": stats["symbols"]},
            "languages": language_names,
            "caps": {
                "names": SEARCH_PAGE_LIMIT,
                "crowded_names": CROWDED_PAGE_LIMIT,
                "paths": PATH_MATCH_LIMIT,
                "matches": MAX_MATCHES,
                "weak": MAX_WEAK,
                "files": MAX_FILE_MATCHES,
            },
        }
        if state.name_omitted:
            coverage["name_omitted"] = state.name_omitted
        # Counted against every distinct matching file, not just the ones the path
        # limit happened to retrieve, so the number is the real shortfall.
        files_omitted = state.files_discovered - len(state.file_matches)
        if files_omitted > 0:
            coverage["files_omitted"] = files_omitted
        if state.path_capped:
            # Some matches were never retrieved at all; the rest of files_omitted is
            # the public cap and the budget, which budget.dropped accounts for.
            coverage["path_capped"] = True
        if scope is not None:
            coverage["path_scope"] = scope
        if symbol_count == 0:
            coverage["reason"] = "empty-index"
        payload["coverage"] = coverage
        payload["payload_complete"] = bool(meta.get("complete"))
        payload["budget"] = meta
        return payload

    def minimal(meta: dict[str, object]) -> dict[str, object]:
        files = FileTable()
        entries: list[dict[str, object]] = [
            {
                "h": symbol_handle(c.symbol.id),
                "n": c.symbol.name,
                "f": files.index(c.symbol.file_path),
                "l": c.symbol.start_line,
            }
            for c in state.matches
        ]
        return {
            "files": files.paths(),
            "matches": entries,
            "coverage": {"scope": "ranked-orientation", "discovered": state.discovered},
            "payload_complete": False,
            "budget": meta,
        }

    def _drop_bridge_example() -> bool:
        for bridge in reversed(state.map_bridges):
            if bridge.examples:
                bridge.examples.pop()
                return True
        return False

    # Bridge examples are the cheapest map evidence to lose and go first. The bridge
    # rows themselves are ~20 bytes and say something no other section does — which
    # areas actually depend on which — so they outlive an extra entrypoint.
    steps: list[DropStep] = []
    for _ in state.map_bridges:
        steps.append(DropStep("map-bridge-example", _drop_bridge_example))
    for _ in state.map_entrypoints:
        steps.append(DropStep("map-entrypoint", lambda: bool(state.map_entrypoints.pop()) or True))
    for _ in state.map_bridges:
        steps.append(DropStep("map-bridge", lambda: bool(state.map_bridges.pop()) or True))
    for _ in state.map_areas:
        steps.append(DropStep("map-area", lambda: bool(state.map_areas.pop()) or True))
    for _ in state.weak:
        steps.append(DropStep("weak", lambda: bool(state.weak.pop()) or True))
    for _ in state.file_matches:
        steps.append(DropStep("file-match", lambda: bool(state.file_matches.pop()) or True))
    if state.crowded:

        def _collapse() -> bool:
            if state.crowded_collapsed:
                return False
            state.crowded_collapsed = True
            return True

        steps.append(DropStep("crowded-terms", _collapse))
    for _ in range(max(0, len(state.matches) - MIN_MATCHES_BEFORE_HARD_CAP)):
        steps.append(DropStep("match", lambda: bool(state.matches.pop()) or True))

    return enforce_budget(assemble, steps, minimal, token_budget=token_budget, shrink_key="matches")
