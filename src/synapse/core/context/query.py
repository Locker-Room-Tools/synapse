"""Orchestration of one bounded, budgeted, deterministic context query."""

from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from synapse.core.context.budget import (
    DEFAULT_TOKEN_BUDGET,
    DropStep,
    clamp_token_budget,
    enforce_budget,
)
from synapse.core.context.keywords import extract_keywords
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


def _build_flows(outcome: TraversalOutcome) -> list[list[str]]:
    """Project root-to-leaf chains over the BFS discovery tree, best evidence first."""
    edges_by_id = {edge.relation.id: edge for edge in outcome.edges}
    candidates = [node for node in outcome.nodes.values() if node.parent_edge_id is not None]

    def leaf_rank(node: TraversedNode) -> tuple[int, int, int, str]:
        edge = edges_by_id[node.parent_edge_id or ""]
        relation = edge.relation
        return (
            -node.depth,
            resolution_rank(relation.resolution),
            confidence_rank(relation.confidence),
            node.symbol.id,
        )

    flows: list[list[str]] = []
    covered: set[str] = set()
    for node in sorted(candidates, key=leaf_rank):
        if node.symbol.id in covered:
            continue
        chain: list[str] = []
        current: TraversedNode | None = node
        while current is not None:
            chain.append(current.symbol.id)
            parent_edge = (
                edges_by_id[current.parent_edge_id] if current.parent_edge_id is not None else None
            )
            parent = _parent_id(parent_edge) if parent_edge is not None else None
            current = outcome.nodes.get(parent) if parent is not None else None
        chain.reverse()
        flows.append(chain)
        covered.update(chain)
        if len(flows) >= MAX_FLOWS:
            break
    return flows


def _node_drop_order(
    outcome: TraversalOutcome,
    protected: set[str],
    *,
    min_depth: int,
    max_depth: int,
) -> list[str]:
    """Order node ids worst-evidence-first for budget drops."""
    edges_by_id = {edge.relation.id: edge for edge in outcome.edges}

    def drop_rank(node: TraversedNode) -> tuple[int, int, int, int, int, str, int, str]:
        edge = edges_by_id.get(node.parent_edge_id or "")
        relation = edge.relation if edge is not None else None
        symbol = node.symbol
        return (
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

    Deterministic for one index state and parameter set. The result is a compact
    JSON string bounded by the clamped token budget; seeds, the primary flow, and
    coverage survive truncation, and truncation or missing coverage is always
    reported explicitly so an empty answer never reads as proof of absence.
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
    flows = _build_flows(outcome)
    protected = set(flows[0]) | seed_ids if flows else set(seed_ids)

    # Mutable projection state consumed by assemble() and the drop steps.
    node_ids = [node_id for node_id in outcome.nodes if node_id not in seed_ids]
    node_set = set(node_ids)
    flow_state = list(flows)
    alternates = list(discovery.alternates)
    imports_state: dict[str, dict[str, object]] = {}
    for file_path in node_files[:MAX_IMPORT_FILES]:
        names = imports_by_file.get(file_path, [])
        if names:
            imports_state[file_path] = {
                "names": names[:MAX_IMPORT_NAMES],
                "total": len(names),
            }
    snippets = _seed_snippets(discovery.seeds, workspace_root) if query.include_source else []

    query_section: dict[str, object] = {
        "question": question,
        "direction": str(query.direction),
        "max_depth": limits.max_depth,
        "token_budget": token_budget,
    }
    if explicit:
        query_section["symbol_ids"] = list(explicit)

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
        resolution_counts: dict[str, int] = {}
        for edge in outcome.edges:
            if edge.relation.kind is RelationKind.REFERENCES:
                label = (
                    str(edge.relation.resolution)
                    if edge.relation.resolution is not None
                    else "unclassified"
                )
                resolution_counts[label] = resolution_counts.get(label, 0) + 1
        projection: dict[str, object] = {
            "flows_are_projections_over_stored_edges": True,
            "nodes_projected": len(node_set) + len(seed_ids & set(outcome.nodes)),
        }
        if query.include_source:
            projection["source_note"] = (
                "snippets read from current disk state; indexed line ranges may drift"
            )
        coverage: dict[str, object] = {
            "index": index_coverage,
            "extraction": _extraction_coverage(languages),
            "traversal": traversal_coverage,
        }
        if resolution_counts:
            coverage["resolution"] = resolution_counts
        coverage["projection"] = projection
        if discovery.unknown_symbol_ids:
            coverage["unknown_symbol_ids"] = list(discovery.unknown_symbol_ids)
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
            payload["flows"] = [list(chain) for chain in flow_state]
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
    for node_id in _node_drop_order(outcome, protected, min_depth=2, max_depth=limits.max_depth):
        steps.append(drop_node(node_id))
    for file_path in reversed(list(imports_state)):
        steps.append(drop_import(file_path))
    for _ in range(min(len(alternates), MIN_ALTERNATES_SHOWN)):
        steps.append(drop_last("alternates", alternates))
    for node_id in _node_drop_order(outcome, protected, min_depth=1, max_depth=1):
        steps.append(drop_node(node_id))

    return enforce_budget(assemble, steps, minimal, token_budget=token_budget)
