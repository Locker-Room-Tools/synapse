"""One-snapshot batch inspection of selected symbols with one-hop evidence."""

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from synapse.core.index import (
    ReadProjections,
    SourceSlice,
    SymbolIndex,
    is_symbol_handle,
    read_symbol_source,
    read_verified_source_window,
    symbol_handle,
)
from synapse.core.languages import (
    call_usage_kinds,
    is_call_usage,
    reference_extraction,
    reference_limitations,
)
from synapse.core.models import Relation, RelationKind, Symbol
from synapse.core.navigation.budget import (
    INSPECT_DEFAULT_TOKEN_BUDGET,
    INSPECT_MIN_TOKEN_BUDGET,
    PUBLIC_MAX_TOKEN_BUDGET,
    DropStep,
    clamp,
    enforce_budget,
)
from synapse.core.navigation.continuation import (
    continuation_token,
    looks_like_continuation,
    parse_continuation,
    source_fingerprint,
)
from synapse.core.navigation.render import FileTable, symbol_ref
from synapse.core.navigation.traversal import edge_sort_key, one_hop

MAX_SYMBOLS = 8
MAX_SOURCE_LINES = 40
# A continuation is the call's explicit ask and carries no relations, children,
# hypotheses, or head source, so it may spend most of the response budget: up to
# 256 lines before wire enforcement, degraded by deterministic halving to the
# same 10-line floor as head source, then honest omission. Passing a token alone
# maximizes the window; mixing it with fresh handles shares the budget and may
# shorten or omit it (the token stays valid — resend it alone for the full window).
MAX_CONTINUATION_LINES = 256
CONTINUATION_FLOOR_LINES = 10
MAX_GROUPS_PER_DIRECTION = 12
MAX_SITES_PER_GROUP = 3
MAX_CHILDREN = 12
MAX_HYPOTHESES = 5
REDUCED_GROUPS = 6
REDUCED_CHILDREN = 5
# Deep-pressure floors: selected-symbol source outranks redundant relation groups, so
# callers/callees shrink to a compact navigation set — and neutral refs keep one group
# per direction — before any selected source is removed. The retained groups follow the
# existing best-evidence-first ordering, so a follow-up relation handle survives
# whenever relation evidence exists, regardless of call classification.
FLOOR_CALL_GROUPS = 2
FLOOR_REF_GROUPS = 1
# One C# extraction block costs ~330 bytes, so an unbounded union would let a polyglot
# workspace spend the whole budget on coverage. Omissions are reported explicitly.
MAX_EXTRACTION_LANGUAGES = 4


@dataclass(frozen=True, slots=True)
class InspectRequest:
    """One batch inspection request: compact handles or internal stable IDs."""

    symbols: tuple[str, ...]
    token_budget: int = INSPECT_DEFAULT_TOKEN_BUDGET


@dataclass(slots=True)
class _Group:
    """References sharing one far endpoint and one call-evidence verdict."""

    endpoint: Symbol | None
    endpoint_name: str | None
    endpoint_file: str | None
    sites: list[Relation]
    total_sites: int
    is_call: bool


@dataclass(slots=True)
class _Selected:
    symbol: Symbol
    handle: str
    parent: Symbol | None = None
    children: list[Symbol] = field(default_factory=list)
    children_total: int = 0
    children_cap: int = MAX_CHILDREN
    src: SourceSlice | None = None
    src_max: int = MAX_SOURCE_LINES
    src_dropped: bool = False
    src_missing: bool = False
    callers: list[_Group] = field(default_factory=list)
    callees: list[_Group] = field(default_factory=list)
    refs_in: list[_Group] = field(default_factory=list)
    refs_out: list[_Group] = field(default_factory=list)
    in_total: int = 0
    out_total: int = 0
    hypotheses: list[Relation] = field(default_factory=list)
    hypotheses_total: int = 0
    dropped: bool = False
    content_hash: str = ""


@dataclass(slots=True)
class _Continuation:
    """One validated source-continuation request and its bounded window."""

    token: str
    symbol: Symbol
    handle: str
    start_line: int
    src: SourceSlice
    content_hash: str
    src_max: int = MAX_CONTINUATION_LINES
    dropped: bool = False


def _group_sort_key(group: _Group) -> tuple[tuple[int, int, int, int, str], int, str]:
    best = min(edge_sort_key(site) for site in group.sites)
    endpoint_id = group.endpoint.id if group.endpoint is not None else f"~{group.endpoint_name}"
    return best, 0 if group.is_call else 1, endpoint_id


def _build_groups(
    relations: list[Relation],
    *,
    endpoint_side: str,
    symbols_by_id: dict[str, Symbol],
    is_call_site: Callable[[Relation], bool],
) -> list[_Group]:
    """Group references by far endpoint AND call evidence.

    Call-ness is part of the grouping key, so an endpoint that is both called and
    named in a type position yields two homogeneous groups. A non-call site can
    therefore never be carried into a call group by a neighbour.
    """
    grouped: dict[tuple[str, bool], _Group] = {}
    for relation in relations:
        fallback_name: str | None
        fallback_file: str | None
        if endpoint_side == "from":
            endpoint_id = relation.from_symbol_id
            fallback_name = None
            fallback_file = relation.from_file_path
        else:
            endpoint_id = relation.to_symbol_id
            fallback_name = relation.to_qualified_name or relation.to_name
            fallback_file = relation.to_file_path
        is_call = is_call_site(relation)
        key = (endpoint_id if endpoint_id is not None else f"~{fallback_name}", is_call)
        group = grouped.get(key)
        if group is None:
            endpoint = symbols_by_id.get(endpoint_id) if endpoint_id is not None else None
            group = _Group(
                endpoint=endpoint,
                endpoint_name=fallback_name,
                endpoint_file=fallback_file,
                sites=[],
                total_sites=0,
                is_call=is_call,
            )
            grouped[key] = group
        group.sites.append(relation)
        group.total_sites += 1
    groups = list(grouped.values())
    for group in groups:
        group.sites.sort(key=edge_sort_key)
    groups.sort(key=_group_sort_key)
    return groups


def _site_entry(relation: Relation, files: FileTable) -> dict[str, object]:
    entry: dict[str, object] = {"f": files.index(relation.from_file_path)}
    if relation.start_line is not None:
        entry["l"] = relation.start_line
    if relation.resolution is not None:
        entry["res"] = str(relation.resolution)
    entry["conf"] = str(relation.confidence)
    if relation.usage_kind is not None:
        entry["use"] = relation.usage_kind
    return entry


def _group_entry(group: _Group, files: FileTable) -> dict[str, object]:
    entry: dict[str, object] = {}
    if group.endpoint is not None:
        entry["h"] = symbol_handle(group.endpoint.id)
        entry["n"] = group.endpoint.name
        entry["f"] = files.index(group.endpoint.file_path)
    else:
        if group.endpoint_name is not None:
            entry["n"] = group.endpoint_name
        if group.endpoint_file is not None:
            entry["f"] = files.index(group.endpoint_file)
    kept = group.sites[:MAX_SITES_PER_GROUP]
    entry["sites"] = [_site_entry(site, files) for site in kept]
    if group.total_sites > len(kept):
        entry["more"] = group.total_sites - len(kept)
    return entry


def _src_truncated(item: _Selected) -> bool:
    """The definition outgrew the fixed slice cap.

    Reads ``SourceSlice.truncated``, which ``read_symbol_source`` sets when the body
    exceeded ``MAX_SOURCE_LINES``. Deliberately independent of ``src_max``: the budget
    lowering that bound is a different cause with its own field, and folding the two
    together reported an under-cap body as fixed-cap-truncated.
    """
    if item.src is None or item.src_dropped:
        return False
    return item.src.truncated


def _src_shortened(item: _Selected) -> bool:
    """The wire budget removed lines the fixed slice would otherwise have contained.

    Requires lines to be actually lost: a body shorter than the reduced bound loses
    nothing, so lowering ``src_max`` alone is not shortening.
    """
    if item.src is None or item.src_dropped or item.src_max >= MAX_SOURCE_LINES:
        return False
    return len(item.src.text.splitlines()) > item.src_max


def _extraction_entry(language: str, produced_evidence: bool) -> dict[str, object]:
    """Call and extraction calibration for one language.

    ``evidence: false`` marks a language that is indexed in this workspace but produced
    none of the returned relations — it is here so a zero-caller answer can be read
    against its call coverage. The key is omitted for evidence-producing languages, so
    the common payload is unchanged.
    """
    entry: dict[str, object] = {
        "language": language,
        "completeness": str(reference_extraction(language)),
        # Empty means this language proves no calls at all, so its usages are reported
        # entirely as neutral refs_in/refs_out.
        "call_kinds": list(call_usage_kinds(language)),
        "limitations": list(reference_limitations(language)),
    }
    if not produced_evidence:
        entry["evidence"] = False
    return entry


def _shown_source_end(start_line: int, text: str, max_lines: int) -> tuple[list[str], int]:
    """The lines actually shown under the current per-symbol cap, and their end line."""
    shown = text.splitlines()[:max_lines]
    return shown, start_line + len(shown) - 1


def _next_token(handle: str, symbol: Symbol, shown_end: int, content_hash: str) -> str | None:
    """Continuation for the first unshown line, or None at the stored span's end."""
    if shown_end >= symbol.end_line:
        return None
    start_line = shown_end + 1
    fingerprint = source_fingerprint(symbol, start_line, content_hash)
    return continuation_token(handle, start_line, fingerprint)


def _continuation_entry(item: _Continuation, files: FileTable) -> dict[str, object]:
    shown, end_line = _shown_source_end(item.start_line, item.src.text, item.src_max)
    more = item.src.truncated or len(shown) < len(item.src.text.splitlines())
    entry: dict[str, object] = {
        "h": item.handle,
        "f": files.index(item.symbol.file_path),
        "lines": [item.start_line, end_line],
        "more": more,
        "text": "\n".join(shown),
    }
    if more:
        token = _next_token(item.handle, item.symbol, end_line, item.content_hash)
        if token is not None:
            entry["next"] = token
            entry["remaining_lines"] = item.symbol.end_line - end_line
    return entry


def _kept_site_count(selected: _Selected) -> int:
    kept = 0
    for group_list in (
        selected.callers,
        selected.callees,
        selected.refs_in,
        selected.refs_out,
    ):
        for group in group_list:
            kept += min(len(group.sites), MAX_SITES_PER_GROUP)
    return kept


@dataclass(frozen=True, slots=True)
class _Evidence:
    """What the returned relations are made of, for honest coverage reporting."""

    selected: list[_Selected]
    missing: list[str]
    # Languages to calibrate, evidence-producing ones first. When a selected symbol has
    # no incoming relations this also carries the other workspace languages, because a
    # caller could have been written in any of them and the agent needs their call
    # coverage to read the zero honestly. `evidence` marks which is which.
    languages: list[str]
    evidence_languages: frozenset[str]
    languages_omitted: int
    continuations: list[_Continuation]
    # (token, reason) pairs; reasons: invalid, unknown-symbol, stale, out-of-range,
    # source-unavailable.
    rejected_continuations: list[tuple[str, str]]


def _build_selected(
    reads: ReadProjections,
    request_keys: tuple[str, ...],
    workspace_root: Path,
) -> _Evidence:
    continuation_keys: list[str] = []
    for key in request_keys:
        if looks_like_continuation(key) and key not in continuation_keys:
            continuation_keys.append(key)
    plain_keys = [key for key in request_keys if not looks_like_continuation(key)]
    handles = [key for key in plain_keys if is_symbol_handle(key)]
    stable_ids = [key for key in plain_keys if not is_symbol_handle(key)]
    by_handle = reads.get_symbols_by_handles(handles) if handles else {}
    by_id = reads.get_symbols_by_ids(stable_ids) if stable_ids else {}
    resolved: dict[str, Symbol] = {}
    missing: list[str] = []
    for key in plain_keys:
        symbol = by_handle.get(key) if is_symbol_handle(key) else by_id.get(key)
        if symbol is None:
            if key not in missing:
                missing.append(key)
        elif symbol.id not in {s.id for s in resolved.values()}:
            resolved[key] = symbol

    selected_ids = [symbol.id for symbol in resolved.values()]
    hop = one_hop(reads, selected_ids)

    endpoint_ids: set[str] = set()
    for symbol in resolved.values():
        if symbol.container_id is not None:
            endpoint_ids.add(symbol.container_id)
        for relation in hop.incoming.get(symbol.id, []):
            if relation.from_symbol_id is not None:
                endpoint_ids.add(relation.from_symbol_id)
        for relation in hop.outgoing.get(symbol.id, []):
            if relation.to_symbol_id is not None:
                endpoint_ids.add(relation.to_symbol_id)
    endpoints = reads.get_symbols_by_ids(sorted(endpoint_ids)) if endpoint_ids else {}

    # A relation carries no language, so call evidence is judged against the language
    # the indexer recorded for the file the usage was written in. That file is
    # `from_file_path` in both directions: the selected symbol's own file for an
    # outgoing reference, the far endpoint's file for an incoming one.
    site_paths = {
        relation.from_file_path
        for symbol in resolved.values()
        for relation in (*hop.incoming.get(symbol.id, []), *hop.outgoing.get(symbol.id, []))
    }
    site_languages = reads.languages_by_path(sorted(site_paths))

    def _is_call_site(relation: Relation) -> bool:
        return is_call_usage(site_languages.get(relation.from_file_path), relation.usage_kind)

    selected: list[_Selected] = []
    for symbol in resolved.values():
        item = _Selected(symbol=symbol, handle=symbol_handle(symbol.id))
        if symbol.container_id is not None:
            item.parent = endpoints.get(symbol.container_id)
        incoming = hop.incoming.get(symbol.id, [])
        outgoing = hop.outgoing.get(symbol.id, [])
        in_references = [r for r in incoming if r.kind is RelationKind.REFERENCES]
        out_references = [r for r in outgoing if r.kind is RelationKind.REFERENCES]
        child_relations = [r for r in outgoing if r.kind is RelationKind.CONTAINS]
        item.in_total = len(in_references)
        item.out_total = len(out_references)
        child_ids = [r.to_symbol_id for r in child_relations if r.to_symbol_id is not None]
        children = [endpoints[cid] for cid in child_ids if cid in endpoints]
        children.sort(key=lambda child: (child.start_byte, child.name))
        item.children_total = len(children)
        item.children = children
        # The cap stays 12 groups per direction; call and non-call groups share it,
        # so the split never doubles the projected evidence.
        kept_incoming = _build_groups(
            in_references,
            endpoint_side="from",
            symbols_by_id=endpoints,
            is_call_site=_is_call_site,
        )[:MAX_GROUPS_PER_DIRECTION]
        item.callers = [group for group in kept_incoming if group.is_call]
        item.refs_in = [group for group in kept_incoming if not group.is_call]
        kept_outgoing = _build_groups(
            out_references,
            endpoint_side="to",
            symbols_by_id=endpoints,
            is_call_site=_is_call_site,
        )[:MAX_GROUPS_PER_DIRECTION]
        item.callees = [group for group in kept_outgoing if group.is_call]
        item.refs_out = [group for group in kept_outgoing if not group.is_call]
        item.src = read_symbol_source(workspace_root, symbol, max_lines=MAX_SOURCE_LINES)
        item.src_missing = item.src is None
        item.hypotheses, item.hypotheses_total = reads.unresolved_references_by_name(
            symbol.name, limit=MAX_HYPOTHESES
        )
        selected.append(item)

    parsed_by_key = {key: parse_continuation(key) for key in continuation_keys}
    continuation_handles = sorted(
        {parsed.handle for parsed in parsed_by_key.values() if parsed is not None}
    )
    continuation_symbols = (
        reads.get_symbols_by_handles(continuation_handles) if continuation_handles else {}
    )
    hash_paths = {item.symbol.file_path for item in selected}
    hash_paths.update(symbol.file_path for symbol in continuation_symbols.values())
    content_hashes = reads.content_hashes_by_path(sorted(hash_paths)) if hash_paths else {}
    for item in selected:
        item.content_hash = content_hashes.get(item.symbol.file_path, "")

    continuations: list[_Continuation] = []
    rejected: list[tuple[str, str]] = []
    for key in continuation_keys:
        parsed = parsed_by_key[key]
        if parsed is None:
            rejected.append((key, "invalid"))
            continue
        symbol = continuation_symbols.get(parsed.handle)
        if symbol is None:
            rejected.append((key, "unknown-symbol"))
            continue
        content_hash = content_hashes.get(symbol.file_path, "")
        if parsed.fingerprint != source_fingerprint(symbol, parsed.start_line, content_hash):
            rejected.append((key, "stale"))
            continue
        # The head slice always starts at the definition, so a valid continuation
        # begins strictly after it and inside the stored span.
        if not symbol.start_line < parsed.start_line <= symbol.end_line:
            rejected.append((key, "out-of-range"))
            continue
        verified = read_verified_source_window(
            workspace_root,
            symbol,
            start_line=parsed.start_line,
            max_lines=MAX_CONTINUATION_LINES,
            content_hash=content_hash,
        )
        if verified.stale:
            # The file changed on disk after indexing: serving this window would
            # splice a newer file version onto a head slice from the older one.
            rejected.append((key, "stale"))
            continue
        if verified.slice is None:
            rejected.append((key, "source-unavailable"))
            continue
        continuations.append(
            _Continuation(
                token=key,
                symbol=symbol,
                handle=parsed.handle,
                start_line=parsed.start_line,
                src=verified.slice,
                content_hash=content_hash,
            )
        )

    evidence_languages = {item.symbol.language for item in selected}
    evidence_languages.update(site_languages.values())
    ordered = sorted(evidence_languages)
    if any(item.in_total == 0 for item in selected):
        # A caller can be written in any indexed language, so a zero-incoming answer is
        # only readable against the call coverage of all of them. They are calibrated in
        # the same structure rather than named in a second one, so the agent never has a
        # language it was told to check but has no metadata for.
        ordered += [
            language
            for language in reads.workspace_languages()
            if language not in evidence_languages
        ]
    return _Evidence(
        selected=selected,
        missing=missing,
        languages=ordered[:MAX_EXTRACTION_LANGUAGES],
        evidence_languages=frozenset(evidence_languages),
        languages_omitted=max(0, len(ordered) - MAX_EXTRACTION_LANGUAGES),
        continuations=continuations,
        rejected_continuations=rejected,
    )


def inspect_symbols(
    index: SymbolIndex,
    request: InspectRequest,
    *,
    workspace_root: Path,
) -> str:
    """Inspect 1-8 selected symbols in one read snapshot.

    Returns definitions, bounded source slices, parents/children, grouped
    callers/callees/other references with stored resolution, confidence, and
    usage kind verbatim, unresolved hypotheses, and endpoint definitions, as a
    compact JSON string never exceeding ``token_budget * 4`` characters.
    """
    if not request.symbols or len(request.symbols) > MAX_SYMBOLS:
        msg = f"synapse_inspect accepts 1-{MAX_SYMBOLS} symbols, got {len(request.symbols)}"
        raise ValueError(msg)
    token_budget = clamp(request.token_budget, INSPECT_MIN_TOKEN_BUDGET, PUBLIC_MAX_TOKEN_BUDGET)

    with index.read_session() as reads:
        evidence = _build_selected(reads, request.symbols, workspace_root)
    selected = evidence.selected
    missing = evidence.missing
    continuations = evidence.continuations
    rejected_continuations = evidence.rejected_continuations
    continuation_requested = len(continuations) + len(rejected_continuations)

    def _symbol_entry(item: _Selected, files: FileTable) -> dict[str, object]:
        symbol = item.symbol
        entry: dict[str, object] = {
            "h": item.handle,
            "n": symbol.qualified_name or symbol.name,
            "k": str(symbol.kind),
            "f": files.index(symbol.file_path),
            "lines": [symbol.start_line, symbol.end_line],
        }
        if symbol.signature is not None:
            entry["sig"] = symbol.signature
        if item.parent is not None:
            entry["parent"] = {
                "h": symbol_handle(item.parent.id),
                "n": item.parent.name,
                "k": str(item.parent.kind),
            }
        if item.children:
            kept_children = item.children[: item.children_cap]
            entry["children"] = [symbol_ref(child, files) for child in kept_children]
            entry["children_total"] = item.children_total
        if item.src is not None and not item.src_dropped:
            end_line = min(item.src.end_line, item.src.start_line + item.src_max - 1)
            lines = item.src.text.splitlines()[: item.src_max]
            src: dict[str, object] = {
                "lines": [item.src.start_line, end_line],
                # Entry-level meaning: the text shown here is incomplete, whatever the
                # cause. Coverage names the causes separately; `shortened` below marks
                # the budget as one of them.
                "truncated": _src_truncated(item) or _src_shortened(item),
                "text": "\n".join(lines),
            }
            if _src_shortened(item):
                src["shortened"] = True
            if src["truncated"]:
                token = _next_token(item.handle, symbol, end_line, item.content_hash)
                if token is not None:
                    src["next"] = token
                    # Cost hint, derived from the same returned end as the token so
                    # the two can never disagree. Present exactly when `next` is.
                    src["remaining_lines"] = symbol.end_line - end_line
            entry["src"] = src
        elif item.src_missing:
            entry["src_unavailable"] = True
        if item.callers:
            entry["callers"] = [_group_entry(group, files) for group in item.callers]
        if item.callees:
            entry["callees"] = [_group_entry(group, files) for group in item.callees]
        if item.refs_in:
            entry["refs_in"] = [_group_entry(group, files) for group in item.refs_in]
        if item.refs_out:
            entry["refs_out"] = [_group_entry(group, files) for group in item.refs_out]
        entry["in_total"] = item.in_total
        in_omitted = item.in_total - sum(
            min(len(g.sites), MAX_SITES_PER_GROUP) for g in (*item.callers, *item.refs_in)
        )
        if in_omitted > 0:
            entry["in_omitted"] = in_omitted
        entry["out_total"] = item.out_total
        out_omitted = item.out_total - sum(
            min(len(g.sites), MAX_SITES_PER_GROUP) for g in (*item.callees, *item.refs_out)
        )
        if out_omitted > 0:
            entry["out_omitted"] = out_omitted
        if item.hypotheses:
            entry["hypotheses"] = [_site_entry(relation, files) for relation in item.hypotheses]
        if item.hypotheses_total > len(item.hypotheses):
            entry["hyp_total"] = item.hypotheses_total
        return entry

    def assemble(meta: dict[str, object]) -> dict[str, object]:
        files = FileTable()
        active = [item for item in selected if not item.dropped]
        symbol_entries = [_symbol_entry(item, files) for item in active]
        payload: dict[str, object] = {"files": files.paths(), "symbols": symbol_entries}
        active_continuations = [item for item in continuations if not item.dropped]
        if active_continuations:
            payload["continuations"] = [
                _continuation_entry(item, files) for item in active_continuations
            ]
        if missing:
            payload["missing"] = list(missing)
        if rejected_continuations:
            payload["continuation_rejected"] = [
                {"token": token, "reason": reason} for token, reason in rejected_continuations
            ]
        relations_returned = sum(_kept_site_count(item) for item in active)
        relations_total = sum(item.in_total + item.out_total for item in selected)
        hypotheses_total = sum(item.hypotheses_total for item in active)
        hypotheses_returned = sum(len(item.hypotheses) for item in active)
        coverage: dict[str, object] = {
            "scope": "selected-symbol-one-hop",
            "selected": len(active),
            "requested": len(request.symbols),
            "resolution_model": "syntactic-structural",
            # Reference extraction is per-language partial by construction; a returned
            # set is never proof that no further evidence exists.
            "exhaustive": False,
            "relations_returned": relations_returned,
            "relations_omitted": max(0, relations_total - relations_returned),
        }
        if hypotheses_total:
            coverage["hypotheses_total"] = hypotheses_total
            coverage["hypotheses_omitted"] = max(0, hypotheses_total - hypotheses_returned)
        if continuation_requested:
            coverage["continuation_requested"] = continuation_requested
            continuation_omitted = sum(1 for item in continuations if item.dropped)
            if continuation_omitted:
                coverage["continuation_omitted"] = continuation_omitted
        for key, handles in (
            ("source_truncated", [i.handle for i in active if _src_truncated(i)]),
            ("source_shortened", [i.handle for i in active if _src_shortened(i)]),
            ("source_omitted", [i.handle for i in active if i.src_dropped]),
            ("source_unavailable", [i.handle for i in active if i.src_missing]),
        ):
            if handles:
                coverage[key] = handles
        if evidence.languages:
            coverage["extraction"] = [
                _extraction_entry(language, language in evidence.evidence_languages)
                for language in evidence.languages
            ]
        if evidence.languages_omitted:
            coverage["extraction_omitted"] = evidence.languages_omitted
        payload["coverage"] = coverage
        payload["payload_complete"] = bool(meta.get("complete"))
        payload["budget"] = meta
        return payload

    def minimal(meta: dict[str, object]) -> dict[str, object]:
        files = FileTable()
        entries: list[dict[str, object]] = [
            {
                "h": item.handle,
                "n": item.symbol.qualified_name or item.symbol.name,
                "f": files.index(item.symbol.file_path),
                "lines": [item.symbol.start_line, item.symbol.end_line],
            }
            for item in selected
        ]
        minimal_coverage: dict[str, object] = {
            "scope": "selected-symbol-one-hop",
            "requested": len(request.symbols),
        }
        if continuation_requested:
            minimal_coverage["continuation_requested"] = continuation_requested
            minimal_coverage["continuation_omitted"] = len(continuations)
        payload: dict[str, object] = {
            "files": files.paths(),
            "symbols": entries,
            "coverage": minimal_coverage,
            "payload_complete": False,
            "budget": meta,
        }
        if missing:
            payload["missing"] = list(missing)
        return payload

    steps = _drop_steps(selected, continuations)
    return enforce_budget(assemble, steps, minimal, token_budget=token_budget, shrink_key="symbols")


def _pop_hypothesis(item: _Selected) -> bool:
    if item.hypotheses:
        item.hypotheses.pop()
        return True
    return False


def _pop_ref_out_group(item: _Selected) -> bool:
    if len(item.refs_out) > FLOOR_REF_GROUPS:
        item.refs_out.pop()
        return True
    return False


def _pop_ref_in_group(item: _Selected) -> bool:
    if len(item.refs_in) > FLOOR_REF_GROUPS:
        item.refs_in.pop()
        return True
    return False


def _pop_callee_group(item: _Selected, floor: int) -> bool:
    if len(item.callees) > floor:
        item.callees.pop()
        return True
    return False


def _pop_caller_group(item: _Selected, floor: int) -> bool:
    if len(item.callers) > floor:
        item.callers.pop()
        return True
    return False


def _shrink_children(item: _Selected) -> bool:
    if item.children_cap > REDUCED_CHILDREN and len(item.children) > REDUCED_CHILDREN:
        item.children_cap = REDUCED_CHILDREN
        return True
    return False


def _halve_source(item: _Selected) -> bool:
    if item.src is None or item.src_dropped or item.src_max <= 10:
        return False
    item.src_max //= 2
    return True


def _drop_source(item: _Selected) -> bool:
    if item.src is None or item.src_dropped:
        return False
    item.src_dropped = True
    return True


def _drop_symbol(item: _Selected) -> bool:
    if item.dropped:
        return False
    item.dropped = True
    return True


def _halve_continuation(item: _Continuation) -> bool:
    if item.dropped or item.src_max <= CONTINUATION_FLOOR_LINES:
        return False
    item.src_max = max(CONTINUATION_FLOOR_LINES, item.src_max // 2)
    return True


def _continuation_halving_steps() -> int:
    """How many halvings reach the floor from the ceiling — never assume a count."""
    steps = 0
    size = MAX_CONTINUATION_LINES
    while size > CONTINUATION_FLOOR_LINES:
        size = max(CONTINUATION_FLOOR_LINES, size // 2)
        steps += 1
    return steps


def _drop_continuation(item: _Continuation) -> bool:
    if item.dropped:
        return False
    item.dropped = True
    return True


def _drop_steps(selected: list[_Selected], continuations: list[_Continuation]) -> list[DropStep]:
    """Deterministic degradation order; earlier-requested symbols degrade last.

    Selected-symbol source outranks redundant relation groups: hypotheses and excess
    neutral references drop first, then source halves, then caller/callee groups shrink
    to `REDUCED_GROUPS` and further to the `FLOOR_CALL_GROUPS` navigation set before any
    selected source is removed. Whole-source drops stay last so a pathological request
    near the symbol maximum still degrades honestly instead of exceeding the cap.

    Continuation windows are the explicit ask of their request, so they halve only
    after every full symbol's source has halved, and a whole continuation drops only
    after whole sources — the agent still holds the token and loses no position,
    while a dropped source has no recovery path inside the surface.
    """
    steps: list[DropStep] = []
    reverse = list(reversed(selected))
    reverse_continuations = list(reversed(continuations))

    def _step(category: str, item: _Selected, apply: Callable[[_Selected], bool]) -> DropStep:
        return DropStep(category, partial(apply, item))

    for item in reverse:
        for _ in item.hypotheses:
            steps.append(_step("hypothesis", item, _pop_hypothesis))
    for item in reverse:
        for _ in range(max(0, len(item.refs_out) - FLOOR_REF_GROUPS)):
            steps.append(_step("ref-group", item, _pop_ref_out_group))
    for item in reverse:
        for _ in range(max(0, len(item.refs_in) - FLOOR_REF_GROUPS)):
            steps.append(_step("ref-group", item, _pop_ref_in_group))
    for item in reverse:
        steps.append(_step("children", item, _shrink_children))
    for item in reverse:
        steps.append(_step("source", item, _halve_source))
        steps.append(_step("source", item, _halve_source))
    for cont in reverse_continuations:
        for _ in range(_continuation_halving_steps()):
            steps.append(DropStep("continuation", partial(_halve_continuation, cont)))
    for item in reverse:
        for _ in range(max(0, len(item.callees) - REDUCED_GROUPS)):
            steps.append(
                _step("callee-group", item, partial(_pop_callee_group, floor=REDUCED_GROUPS))
            )
    for item in reverse:
        for _ in range(max(0, len(item.callers) - REDUCED_GROUPS)):
            steps.append(
                _step("caller-group", item, partial(_pop_caller_group, floor=REDUCED_GROUPS))
            )
    for item in reverse:
        for _ in range(max(0, min(len(item.callees), REDUCED_GROUPS) - FLOOR_CALL_GROUPS)):
            steps.append(
                _step("callee-group", item, partial(_pop_callee_group, floor=FLOOR_CALL_GROUPS))
            )
    for item in reverse:
        for _ in range(max(0, min(len(item.callers), REDUCED_GROUPS) - FLOOR_CALL_GROUPS)):
            steps.append(
                _step("caller-group", item, partial(_pop_caller_group, floor=FLOOR_CALL_GROUPS))
            )
    for item in reverse:
        steps.append(_step("source", item, _drop_source))
    for cont in reverse_continuations:
        steps.append(DropStep("continuation", partial(_drop_continuation, cont)))
    for item in reverse[:-1]:
        steps.append(_step("symbol", item, _drop_symbol))
    return steps
