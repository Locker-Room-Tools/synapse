"""Orchestration of one bounded, budgeted, deterministic context query."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from synapse.core.context.budget import (
    DEFAULT_TOKEN_BUDGET,
    DropStep,
    clamp_token_budget,
    enforce_budget,
)
from synapse.core.context.keywords import QueryKeywords, extract_keywords
from synapse.core.context.seeds import (
    Seed,
    SeedDiscovery,
    discover_seeds,
    is_test_path,
    kind_rank,
)
from synapse.core.context.traversal import (
    Direction,
    TraversalLimits,
    TraversalOutcome,
    TraversedEdge,
    TraversedNode,
    confidence_rank,
    edge_trust,
    resolution_rank,
    traverse,
)
from synapse.core.index import SymbolIndex
from synapse.core.indexing import reference_index_is_stale
from synapse.core.languages import reference_extraction, reference_limitations
from synapse.core.models import RelationKind
from synapse.core.workspace import read_metadata

_T = TypeVar("_T")

MAX_FLOWS = 3
MAX_IMPORT_FILES = 30
MAX_IMPORT_NAMES = 15
MAX_SOURCE_SEEDS = 3
MAX_SOURCE_LINES = 20
MIN_ALTERNATES_SHOWN = 3
MAX_QUESTION_ECHO = 240
MAX_ID_ECHO = 120
MAX_IDS_ECHOED = 10
MAX_EXTRA_EDGES = 30
MAX_UNRESOLVED_SHOWN = 10
MAX_TEST_NODES = 5
MAX_CONTAINED_MEMBERS_PER_CONTAINER = 3
MIN_RELEVANCE_TOKEN_LENGTH = 3

# Data-shaped members whose containment children rarely answer a question on
# their own; capped per container unless question-relevant.
_LOW_VALUE_MEMBER_KINDS = frozenset({"field", "constant", "variable", "property"})


def _bounded_text(value: str, cap: int) -> str:
    """Deterministically truncate one user-controlled string for echoing."""
    return value if len(value) <= cap else value[: cap - 1] + "…"


def _bounded_id_echo(ids: Sequence[str]) -> list[str]:
    return [_bounded_text(value, MAX_ID_ECHO) for value in ids[:MAX_IDS_ECHOED]]


@dataclass(frozen=True, slots=True)
class ContextQuery:
    """Caller-facing parameters for one context query."""

    question: str
    symbol_ids: tuple[str, ...] = ()
    direction: Direction = Direction.BOTH
    max_depth: int = 3
    token_budget: int = DEFAULT_TOKEN_BUDGET
    include_source: bool = False


def _seed_payload(seed: Seed) -> dict[str, object]:
    symbol = seed.symbol
    payload: dict[str, object] = {
        "id": symbol.id,
        "kind": str(symbol.kind),
        "name": symbol.name,
    }
    if symbol.qualified_name and symbol.qualified_name != symbol.name:
        payload["qualified"] = symbol.qualified_name
    payload["file"] = symbol.file_path
    payload["lines"] = [symbol.start_line, symbol.end_line]
    if symbol.signature:
        payload["sig"] = symbol.signature
    payload["match"] = str(seed.match)
    if is_test_path(symbol.file_path):
        payload["test_path"] = True
    return payload


def _alternate_payload(seed: Seed) -> dict[str, object]:
    symbol = seed.symbol
    return {"id": symbol.id, "name": symbol.name, "file": symbol.file_path}


def _via_payload(edge: TraversedEdge) -> dict[str, object]:
    relation = edge.relation
    via: dict[str, object] = {"edge": str(relation.kind), "dir": edge.direction}
    if relation.resolution is not None:
        via["res"] = str(relation.resolution)
    via["conf"] = str(relation.confidence)
    peer = relation.from_symbol_id if edge.direction == "out" else relation.to_symbol_id
    if peer is not None:
        via["peer"] = peer
    if relation.start_line is not None:
        via["at"] = f"{relation.from_file_path}:{relation.start_line}"
    if relation.usage_kind is not None:
        via["usage"] = relation.usage_kind
    return via


def _node_payload(node: TraversedNode, edge: TraversedEdge | None) -> dict[str, object]:
    symbol = node.symbol
    payload: dict[str, object] = {
        "id": symbol.id,
        "kind": str(symbol.kind),
        "name": symbol.name,
    }
    if symbol.qualified_name and symbol.qualified_name != symbol.name:
        payload["qualified"] = symbol.qualified_name
    payload["file"] = symbol.file_path
    payload["lines"] = [symbol.start_line, symbol.end_line]
    payload["depth"] = node.depth
    if edge is not None:
        payload["via"] = _via_payload(edge)
    return payload


def _parent_id(edge: TraversedEdge) -> str | None:
    relation = edge.relation
    return relation.from_symbol_id if edge.direction == "out" else relation.to_symbol_id


def _extra_edge_payload(edge: TraversedEdge) -> dict[str, object]:
    """Compact projection of a discovered non-tree edge (cross-link or cycle)."""
    relation = edge.relation
    payload: dict[str, object] = {
        "from": relation.from_symbol_id,
        "to": relation.to_symbol_id,
        "edge": str(relation.kind),
    }
    if relation.resolution is not None:
        payload["res"] = str(relation.resolution)
    payload["conf"] = str(relation.confidence)
    if relation.start_line is not None:
        payload["at"] = f"{relation.from_file_path}:{relation.start_line}"
    return payload


def _unresolved_items(outcome: TraversalOutcome) -> list[dict[str, object]]:
    """Seed-level references the index could not bind to one target, as evidence.

    Each entry names the referenced symbol, the stored resolution (ambiguous or
    unresolved), and the source site, so a caller can drill down with a targeted
    definition lookup instead of mistaking the gap for absence.
    """
    best: dict[str, dict[str, object]] = {}
    for relation in outcome.null_target_references:
        if relation.to_name is None:
            continue
        entry: dict[str, object] = {
            "name": relation.to_name,
            "res": str(relation.resolution) if relation.resolution is not None else "unresolved",
        }
        if relation.start_line is not None:
            entry["at"] = f"{relation.from_file_path}:{relation.start_line}"
        existing = best.get(relation.to_name)
        if existing is None:
            best[relation.to_name] = entry
    ranked = sorted(
        best.values(),
        key=lambda item: (0 if item["res"] == "ambiguous" else 1, str(item["name"])),
    )
    return ranked[:MAX_UNRESOLVED_SHOWN]


def _ranked_extra_edges(outcome: TraversalOutcome) -> list[TraversedEdge]:
    """Non-tree edges (not any node's discovery edge), best evidence first."""
    parent_edge_ids = {
        node.parent_edge_id for node in outcome.nodes.values() if node.parent_edge_id is not None
    }

    def edge_rank(edge: TraversedEdge) -> tuple[int, int, int, str]:
        relation = edge.relation
        return (
            0 if relation.kind is RelationKind.CONTAINS else resolution_rank(relation.resolution),
            confidence_rank(relation.confidence),
            edge.depth,
            relation.id,
        )

    extras = [edge for edge in outcome.edges if edge.relation.id not in parent_edge_ids]
    return sorted(extras, key=edge_rank)


_TRUST_ORDER = ("exact", "scoped", "heuristic")


@dataclass(frozen=True, slots=True)
class Flow:
    """One root-to-leaf chain with its aggregate path trust."""

    ids: tuple[str, ...]
    trust: str


def _relevant_node_ids(
    outcome: TraversalOutcome, seed_ids: set[str], keywords: QueryKeywords
) -> set[str]:
    """Seeds plus nodes whose own name carries a question token.

    Deliberately narrower than seed matching: the qualified name is excluded, so a
    matched container does not bless every member it contains as relevant.
    """
    tokens = {
        token.lower()
        for token in (*keywords.identifiers, *keywords.terms)
        if len(token) >= MIN_RELEVANCE_TOKEN_LENGTH
    }
    relevant = set(seed_ids)
    if not tokens:
        return relevant
    for node_id, node in outcome.nodes.items():
        name = node.symbol.name.lower()
        if any(token in name for token in tokens):
            relevant.add(node_id)
    return relevant


def _build_flows(outcome: TraversalOutcome, *, relevant_ids: set[str]) -> tuple[list[Flow], bool]:
    """Project root-to-leaf chains over the BFS discovery tree, best evidence first.

    Ranking uses aggregate path trust before depth: a chain's trust is its weakest
    edge, so one heuristic hop marks the whole flow heuristic and a long weak path
    never outranks a shorter exact/scoped path. Among equally trusted chains,
    question relevance decides. A chain must be substantive — carry at least one
    reference edge or end at a question-relevant symbol; pure containment descent
    into unmatched members is structure, not an answer, and projects as nodes only.
    Returns the flows and whether chains existed but none was substantive. Chains
    routed through test code rank below production chains at equal trust — ranking
    choices only; stored facts are unchanged.
    """
    edges_by_id = {edge.relation.id: edge for edge in outcome.edges}

    def path_edges(node: TraversedNode) -> tuple[list[str], list[TraversedEdge]]:
        chain: list[str] = []
        edges: list[TraversedEdge] = []
        current: TraversedNode | None = node
        while current is not None:
            chain.append(current.symbol.id)
            parent_edge = (
                edges_by_id[current.parent_edge_id] if current.parent_edge_id is not None else None
            )
            if parent_edge is not None:
                edges.append(parent_edge)
            parent = _parent_id(parent_edge) if parent_edge is not None else None
            current = outcome.nodes.get(parent) if parent is not None else None
        chain.reverse()
        return chain, edges

    chains_existed = False
    ranked_chains: list[tuple[tuple[int, int, int, int, int, int, str], Flow]] = []
    for node in outcome.nodes.values():
        if node.parent_edge_id is None:
            continue
        chains_existed = True
        chain, chain_edges = path_edges(node)
        substantive = (
            any(edge.relation.kind is RelationKind.REFERENCES for edge in chain_edges)
            or chain[-1] in relevant_ids
        )
        if not substantive:
            continue
        trusts = [edge_trust(edge.relation) for edge in chain_edges]
        heuristic_hops = sum(1 for trust in trusts if trust == "heuristic")
        worst_resolution = max(
            resolution_rank(edge.relation.resolution)
            if edge.relation.kind is not RelationKind.CONTAINS
            else 0
            for edge in chain_edges
        )
        worst_confidence = max(confidence_rank(edge.relation.confidence) for edge in chain_edges)
        test_penalty = sum(
            1 for node_id in chain if is_test_path(outcome.nodes[node_id].symbol.file_path)
        )
        relevance = sum(1 for node_id in chain if node_id in relevant_ids)
        rank = (
            heuristic_hops,
            worst_resolution,
            test_penalty,
            -relevance,
            -node.depth,
            worst_confidence,
            node.symbol.id,
        )
        trust_label = max(trusts, key=_TRUST_ORDER.index)
        ranked_chains.append((rank, Flow(ids=tuple(chain), trust=trust_label)))

    flows: list[Flow] = []
    covered: set[str] = set()
    for _, flow in sorted(ranked_chains, key=lambda entry: entry[0]):
        if flow.ids[-1] in covered:
            continue
        flows.append(flow)
        covered.update(flow.ids)
        if len(flows) >= MAX_FLOWS:
            break
    return flows, chains_existed and not flows


def _node_drop_order(
    outcome: TraversalOutcome,
    protected: set[str],
    *,
    min_depth: int,
    max_depth: int,
    tests_first: bool = False,
) -> list[str]:
    """Order node ids worst-evidence-first for budget drops.

    With tests_first (production-focus policy), test-path nodes drop before any
    comparable production evidence regardless of depth.
    """
    edges_by_id = {edge.relation.id: edge for edge in outcome.edges}

    def drop_rank(node: TraversedNode) -> tuple[int, int, int, int, int, int, str, int, str]:
        edge = edges_by_id.get(node.parent_edge_id or "")
        relation = edge.relation if edge is not None else None
        symbol = node.symbol
        return (
            1 if tests_first and is_test_path(symbol.file_path) else 0,
            node.depth,
            resolution_rank(relation.resolution if relation is not None else None),
            confidence_rank(relation.confidence) if relation is not None else 0,
            1 if is_test_path(symbol.file_path) else 0,
            kind_rank(symbol.kind),
            symbol.file_path,
            symbol.start_line,
            symbol.id,
        )

    candidates = [
        node
        for node in outcome.nodes.values()
        if min_depth <= node.depth <= max_depth and node.symbol.id not in protected
    ]
    return [node.symbol.id for node in sorted(candidates, key=drop_rank, reverse=True)]


def _low_relevance_demotions(
    outcome: TraversalOutcome, *, protected: set[str], relevant_ids: set[str]
) -> list[str]:
    """Node ids to demote upfront as policy, regardless of remaining budget.

    Two shapes of low-value evidence: data-member swarms (more than
    MAX_CONTAINED_MEMBERS_PER_CONTAINER contained fields of one container) and deep
    heuristic leaves (depth >= 2 reached only through a heuristic edge). Question-
    relevant and protected nodes are never demoted; everything demoted is counted in
    coverage, so omission stays visible.
    """
    edges_by_id = {edge.relation.id: edge for edge in outcome.edges}
    demoted: list[str] = []
    members_by_container: dict[str, list[TraversedNode]] = {}
    for node in outcome.nodes.values():
        node_id = node.symbol.id
        if node_id in protected or node_id in relevant_ids or node.parent_edge_id is None:
            continue
        edge = edges_by_id.get(node.parent_edge_id)
        if edge is None:
            continue
        if node.depth >= 2 and edge_trust(edge.relation) == "heuristic":
            demoted.append(node_id)
            continue
        parent = _parent_id(edge)
        if (
            parent is not None
            and edge.relation.kind is RelationKind.CONTAINS
            and str(node.symbol.kind) in _LOW_VALUE_MEMBER_KINDS
        ):
            members_by_container.setdefault(parent, []).append(node)
    for members in members_by_container.values():
        members.sort(
            key=lambda node: (
                kind_rank(node.symbol.kind),
                node.symbol.file_path,
                node.symbol.start_line,
                node.symbol.id,
            )
        )
        demoted.extend(node.symbol.id for node in members[MAX_CONTAINED_MEMBERS_PER_CONTAINER:])
    return sorted(set(demoted))


def _seed_snippets(seeds: tuple[Seed, ...], workspace_root: Path) -> list[dict[str, object]]:
    snippets: list[dict[str, object]] = []
    for seed in seeds[:MAX_SOURCE_SEEDS]:
        symbol = seed.symbol
        path = workspace_root / symbol.file_path
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if symbol.start_line < 1 or symbol.start_line > len(lines):
            continue
        start = symbol.start_line
        end = min(symbol.end_line, len(lines), start + MAX_SOURCE_LINES - 1)
        snippets.append(
            {
                "id": symbol.id,
                "lines": [start, end],
                "text": "\n".join(lines[start - 1 : end]),
            }
        )
    return snippets


def _extraction_coverage(languages: list[str]) -> list[dict[str, object]]:
    coverage: list[dict[str, object]] = []
    for language in languages:
        entry: dict[str, object] = {
            "language": language,
            "references": str(reference_extraction(language)),
        }
        limitations = reference_limitations(language)
        if limitations:
            entry["limitations"] = list(limitations)
        coverage.append(entry)
    return coverage


def _zero_result_reason(
    symbol_count: int, discovery: SeedDiscovery, explicit: tuple[str, ...]
) -> str | None:
    if discovery.seeds:
        return None
    if symbol_count == 0:
        return "empty-index"
    if explicit and discovery.unknown_symbol_ids:
        return "unknown-symbol-ids"
    return "no-seed-match"


def query_context(index: SymbolIndex, query: ContextQuery, *, workspace_root: Path) -> str:
    """Answer one context question with a ranked, budgeted evidence bundle.

    Deterministic for one index state and parameter set. The hard guarantee is on
    size: the returned string never exceeds the CLAMPED token budget times 4
    characters and is always valid JSON (`token_budget` itself is an estimate, not
    an exact model-token count). Normal budget reduction preserves high-priority
    evidence — seeds, the primary flow, coverage — as long as it fits; under extreme
    pressure the result shrinks to a minimal envelope that may trim seeds, and as a
    last resort to a truncation-only envelope that always carries `complete: false`,
    so a truncated result can never look complete or read as proof of absence.
    """
    question = query.question.strip()
    explicit = tuple(dict.fromkeys(query.symbol_ids))
    if not question and not explicit:
        msg = "Provide a question, explicit symbol_ids, or both."
        raise ValueError(msg)
    token_budget = clamp_token_budget(query.token_budget)
    limits = TraversalLimits(max_depth=query.max_depth).clamped()

    keywords = extract_keywords(question)
    stale = reference_index_is_stale(workspace_root)
    metadata = read_metadata(workspace_root)

    with index.read_session() as reads:
        stats = reads.workspace_stats()
        discovery = discover_seeds(reads, keywords, explicit)
        outcome = traverse(
            reads,
            [seed.symbol for seed in discovery.seeds],
            query.direction,
            limits,
        )
        node_files: list[str] = []
        for node in outcome.nodes.values():
            if node.symbol.file_path not in node_files:
                node_files.append(node.symbol.file_path)
        imports_by_file = reads.imports_for_files(node_files[:MAX_IMPORT_FILES])

    symbol_count = int(stats["symbols"]) if isinstance(stats["symbols"], int) else 0
    seed_ids = {seed.symbol.id for seed in discovery.seeds}
    relevant_ids = _relevant_node_ids(outcome, seed_ids, keywords)
    flows, flows_omitted_for_relevance = _build_flows(outcome, relevant_ids=relevant_ids)
    protected = set(flows[0].ids) | seed_ids if flows else set(seed_ids)

    # Projection policy: a query about test symbols keeps full test evidence;
    # everything else is production-focused, with test evidence demoted and capped.
    test_relevant = any(is_test_path(seed.symbol.file_path) for seed in discovery.seeds)
    policy = "test-relevant" if test_relevant else "production-focus"

    # Mutable projection state consumed by assemble() and the drop steps.
    node_ids = [node_id for node_id in outcome.nodes if node_id not in seed_ids]
    node_set = set(node_ids)
    tests_discovered = sum(
        1 for node_id in node_ids if is_test_path(outcome.nodes[node_id].symbol.file_path)
    )
    tests_demoted = 0
    if policy == "production-focus" and tests_discovered > MAX_TEST_NODES:
        for node_id in _node_drop_order(
            outcome, protected, min_depth=1, max_depth=limits.max_depth, tests_first=True
        ):
            if tests_discovered - tests_demoted <= MAX_TEST_NODES:
                break
            if node_id in node_set and is_test_path(outcome.nodes[node_id].symbol.file_path):
                node_set.discard(node_id)
                tests_demoted += 1

    # Relevance policy: low-value evidence is demoted even when budget remains —
    # the budget is a maximum, not a target. Counted in coverage.projection.
    low_relevance_demoted = [
        node_id
        for node_id in _low_relevance_demotions(
            outcome, protected=protected, relevant_ids=relevant_ids
        )
        if node_id in node_set
    ]
    node_set.difference_update(low_relevance_demoted)
    flows = [
        flow
        for index_position, flow in enumerate(flows)
        if index_position == 0 or flow.ids[-1] in node_set | seed_ids
    ]
    flow_state = list(flows)
    alternates = list(discovery.alternates)
    ranked_extras = _ranked_extra_edges(outcome)
    deduped_extras: list[TraversedEdge] = []
    edges_by_id = {edge.relation.id: edge for edge in outcome.edges}
    # An extra edge repeating a tree edge's endpoints and kind (another call site of
    # the same link) is the evidence the node's `via` already shows; dedup both ways.
    seen_extra_keys: set[tuple[str | None, str | None, str]] = {
        (tree.relation.from_symbol_id, tree.relation.to_symbol_id, str(tree.relation.kind))
        for node in outcome.nodes.values()
        if node.parent_edge_id is not None
        and (tree := edges_by_id.get(node.parent_edge_id)) is not None
    }
    for edge in ranked_extras:
        key = (
            edge.relation.from_symbol_id,
            edge.relation.to_symbol_id,
            str(edge.relation.kind),
        )
        if key in seen_extra_keys:
            continue
        seen_extra_keys.add(key)
        deduped_extras.append(edge)
    extra_edges_deduped = len(ranked_extras) - len(deduped_extras)
    extra_edges_state = deduped_extras[:MAX_EXTRA_EDGES]
    extra_edges_capped = len(deduped_extras) - len(extra_edges_state)
    unresolved_state = _unresolved_items(outcome)

    def projected_extra_edges() -> list[TraversedEdge]:
        projected = seed_ids | node_set
        return [
            edge
            for edge in extra_edges_state
            if edge.relation.from_symbol_id in projected and edge.relation.to_symbol_id in projected
        ]

    imports_state: dict[str, dict[str, object]] = {}
    for file_path in node_files[:MAX_IMPORT_FILES]:
        names = imports_by_file.get(file_path, [])
        if names:
            imports_state[file_path] = {
                "names": names[:MAX_IMPORT_NAMES],
                "total": len(names),
            }
    snippets = _seed_snippets(discovery.seeds, workspace_root) if query.include_source else []

    question_echo = _bounded_text(question, MAX_QUESTION_ECHO)
    query_section: dict[str, object] = {
        "question": question_echo,
        "direction": str(query.direction),
        "max_depth": limits.max_depth,
        "token_budget": token_budget,
    }
    if question_echo != question:
        query_section["question_truncated"] = True
    if explicit:
        query_section["symbol_ids"] = _bounded_id_echo(explicit)
        if len(explicit) > MAX_IDS_ECHOED:
            query_section["symbol_ids_total"] = len(explicit)

    zero_result = _zero_result_reason(symbol_count, discovery, explicit)
    languages: list[str] = []
    raw_languages = stats["languages"]
    if isinstance(raw_languages, list):
        for entry in raw_languages:
            if isinstance(entry, dict):
                languages.append(str(entry["language"]))

    def coverage_section() -> dict[str, object]:
        index_coverage: dict[str, object] = {
            "files": stats["files"],
            "symbols": stats["symbols"],
            "stale": stale,
        }
        if metadata is not None and metadata.last_indexed_at is not None:
            index_coverage["last_indexed_at"] = metadata.last_indexed_at
        traversal_coverage: dict[str, object] = {
            "depth_requested": limits.max_depth,
            "depth_reached": outcome.depth_reached,
            "nodes_discovered": len(outcome.nodes),
            "edges_discovered": len(outcome.edges),
        }
        stopped_by = [name for name, tripped in outcome.guards.items() if tripped]
        if stopped_by:
            traversal_coverage["stopped_by"] = stopped_by
        if outcome.frontier_remaining:
            traversal_coverage["frontier_remaining"] = outcome.frontier_remaining
        if outcome.suppressed:
            traversal_coverage["fanout_suppressed"] = {
                "nodes": len(outcome.suppressed),
                "edges": sum(outcome.suppressed.values()),
            }
        if outcome.unresolved_edges:
            traversal_coverage["unresolved_edges"] = outcome.unresolved_edges
        if outcome.dangling_targets:
            traversal_coverage["dangling_targets"] = outcome.dangling_targets
        if outcome.heuristic_leaf_edges:
            traversal_coverage["not_expanded_heuristic"] = outcome.heuristic_leaf_edges
        resolution_counts: dict[str, int] = {}
        for edge in outcome.edges:
            if edge.relation.kind is RelationKind.REFERENCES:
                label = (
                    str(edge.relation.resolution)
                    if edge.relation.resolution is not None
                    else "unclassified"
                )
                resolution_counts[label] = resolution_counts.get(label, 0) + 1
        tree_projected = sum(
            1 for node_id in node_set if outcome.nodes[node_id].parent_edge_id is not None
        )
        extra_projected = len(projected_extra_edges())
        tests_projected = sum(
            1 for node_id in node_set if is_test_path(outcome.nodes[node_id].symbol.file_path)
        )
        projection: dict[str, object] = {
            "flows_are_projections_over_stored_edges": True,
            "policy": policy,
            "nodes_projected": len(node_set) + len(seed_ids & set(outcome.nodes)),
            "edges": {
                "discovered": len(outcome.edges),
                "tree_projected": tree_projected,
                "extra_projected": extra_projected,
                "omitted": len(outcome.edges) - tree_projected - extra_projected,
            },
            "tests": {
                "discovered": tests_discovered,
                "projected": tests_projected,
                "demoted": tests_discovered - tests_projected,
            },
        }
        if low_relevance_demoted:
            projection["nodes_demoted_low_relevance"] = len(low_relevance_demoted)
        if extra_edges_deduped:
            projection["extra_edges_deduped"] = extra_edges_deduped
        if flows_omitted_for_relevance:
            projection["flows_omitted"] = "no-relevant-flow"
        if extra_edges_capped:
            projection["extra_edges_capped"] = extra_edges_capped
        if query.include_source:
            projection["source_note"] = (
                "snippets read from current disk state; indexed line ranges may drift"
            )
        seed_coverage: dict[str, object] = {"origin": str(discovery.origin)}
        if discovery.fallback_reason is not None:
            seed_coverage["fallback_reason"] = discovery.fallback_reason
        if discovery.seeds and all(is_test_path(seed.symbol.file_path) for seed in discovery.seeds):
            seed_coverage["only_test_matches"] = True
        coverage: dict[str, object] = {
            "index": index_coverage,
            "seeds": seed_coverage,
            "extraction": _extraction_coverage(languages),
            "traversal": traversal_coverage,
        }
        if resolution_counts:
            coverage["resolution"] = resolution_counts
        coverage["projection"] = projection
        if discovery.unknown_symbol_ids:
            coverage["unknown_symbol_ids"] = _bounded_id_echo(discovery.unknown_symbol_ids)
            if len(discovery.unknown_symbol_ids) > MAX_IDS_ECHOED:
                coverage["unknown_symbol_ids_total"] = len(discovery.unknown_symbol_ids)
        if zero_result is not None:
            coverage["zero_result"] = zero_result
        return coverage

    def assemble(truncation: dict[str, object]) -> dict[str, object]:
        payload: dict[str, object] = {"query": query_section}
        payload["seeds"] = [_seed_payload(seed) for seed in discovery.seeds]
        if alternates:
            payload["alternates"] = {
                "items": [_alternate_payload(seed) for seed in alternates],
                "total": discovery.total_candidates,
                "shown": len(alternates),
            }
        if node_set:
            edges_by_id = {edge.relation.id: edge for edge in outcome.edges}
            payload["nodes"] = [
                _node_payload(
                    outcome.nodes[node_id],
                    edges_by_id.get(outcome.nodes[node_id].parent_edge_id or ""),
                )
                for node_id in outcome.nodes
                if node_id in node_set
            ]
        if flow_state:
            payload["flows"] = [{"ids": list(flow.ids), "trust": flow.trust} for flow in flow_state]
        projected_extras = projected_extra_edges()
        if projected_extras:
            payload["edges"] = [_extra_edge_payload(edge) for edge in projected_extras]
        if unresolved_state:
            payload["unresolved"] = {
                "items": list(unresolved_state),
                "total": outcome.unresolved_edges,
            }
        if imports_state:
            payload["imports"] = dict(imports_state)
        if snippets:
            payload["source"] = list(snippets)
        payload["coverage"] = coverage_section()
        payload["truncation"] = truncation
        return payload

    def minimal(truncation: dict[str, object]) -> dict[str, object]:
        payload: dict[str, object] = {
            "query": query_section,
            "seeds": [
                {"id": seed.symbol.id, "file": seed.symbol.file_path} for seed in discovery.seeds
            ],
            "coverage": {
                "index": {
                    "files": stats["files"],
                    "symbols": stats["symbols"],
                    "stale": stale,
                },
            },
            "truncation": truncation,
        }
        if zero_result is not None:
            coverage = payload["coverage"]
            if isinstance(coverage, dict):
                coverage["zero_result"] = zero_result
        return payload

    def drop_node(node_id: str) -> DropStep:
        def apply_once() -> bool:
            if node_id in node_set:
                node_set.discard(node_id)
                return True
            return False

        return DropStep(category="nodes", apply=apply_once)

    def drop_last(category: str, items: list[_T]) -> DropStep:
        def apply_once() -> bool:
            if items:
                items.pop()
                return True
            return False

        return DropStep(category=category, apply=apply_once)

    def drop_import(file_path: str) -> DropStep:
        def apply_once() -> bool:
            return imports_state.pop(file_path, None) is not None

        return DropStep(category="imports", apply=apply_once)

    steps: list[DropStep] = []
    for _ in range(max(0, len(flow_state) - 1)):
        steps.append(drop_last("flows", flow_state))
    for _ in range(max(0, len(alternates) - MIN_ALTERNATES_SHOWN)):
        steps.append(drop_last("alternates", alternates))
    for _ in range(len(snippets)):
        steps.append(drop_last("snippets", snippets))
    tests_drop_first = policy == "production-focus"
    for node_id in _node_drop_order(
        outcome,
        protected,
        min_depth=2,
        max_depth=limits.max_depth,
        tests_first=tests_drop_first,
    ):
        steps.append(drop_node(node_id))
    for file_path in reversed(list(imports_state)):
        steps.append(drop_import(file_path))
    for _ in range(len(extra_edges_state)):
        steps.append(drop_last("edges", extra_edges_state))
    for _ in range(len(unresolved_state)):
        steps.append(drop_last("unresolved", unresolved_state))
    for _ in range(min(len(alternates), MIN_ALTERNATES_SHOWN)):
        steps.append(drop_last("alternates", alternates))
    for node_id in _node_drop_order(
        outcome, protected, min_depth=1, max_depth=1, tests_first=tests_drop_first
    ):
        steps.append(drop_node(node_id))

    return enforce_budget(assemble, steps, minimal, token_budget=token_budget)
