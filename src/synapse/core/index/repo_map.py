"""Materialized repository map: bounded areas, entrypoints, anchors, and bridges.

The map is a deterministic projection over stored paths, containment, imports, and
exact/scoped references. It is orientation evidence for broad questions — never
proof of exhaustive architecture. It is stored as one JSON blob in ``index_meta``
and versioned independently of the schema so a derivation change rebuilds stale
maps without forcing a workspace reindex.
"""

import json
import sqlite3
from dataclasses import dataclass

from synapse.core.index.reads import TOP_SYMBOL_KINDS, ReadProjections
from synapse.core.index.writes import set_meta
from synapse.core.models import Symbol, SymbolKind

REPO_MAP_KEY = "repo_map"
REPO_MAP_VERSION_KEY = "repo_map_version"
# Bump when the derivation algorithm, its signals, its bounds, or the JSON shape
# change; stored maps with another version are ignored and rebuilt on next index.
REPO_MAP_DERIVATION_VERSION = 2

MAX_AREAS = 12
MIN_TARGET_AREAS = 4
# Target roughly one area per this many files, within [MIN_TARGET_AREAS, MAX_AREAS]:
# a mid-size repository stays coarse instead of exploding into per-feature
# micro-areas, while tiny repositories still separate their few directories.
AREA_TARGET_DIVISOR = 8
MAX_ANCHORS_PER_AREA = 5
MAX_ENTRYPOINTS = 10
MAX_BRIDGES = 20
MAX_BRIDGE_EXAMPLES = 2

TRUSTED_DEGREE_LIMIT = 500
FALLBACK_ANCHOR_POOL_LIMIT = 200
ENTRYPOINT_FILE_CANDIDATES = 25

# Generic, language-agnostic conventions only — never repository-specific names.
ENTRYPOINT_SYMBOL_NAMES = ("main", "run", "serve", "start", "cli", "launch")
ENTRYPOINT_FILE_STEMS = frozenset({"main", "__main__", "program", "app", "cli", "server", "index"})

_TEST_DIRECTORY_SEGMENTS = frozenset({"test", "tests", "testing", "__tests__", "spec", "specs"})
_GENERATED_DIRECTORY_SEGMENTS = frozenset(
    {"migrations", "migration", "generated", "snapshots", "__snapshots__"}
)
_GENERATED_FILE_MARKERS = (".designer.", ".g.", ".generated.", ".min.")

_KIND_RANKS: dict[str, int] = {str(kind): rank for rank, kind in enumerate(TOP_SYMBOL_KINDS)}
_UNRANKED_KIND = len(TOP_SYMBOL_KINDS)

_ENTRYPOINT_CALLABLE_KINDS = frozenset({SymbolKind.FUNCTION, SymbolKind.METHOD})


def kind_rank(kind: object) -> int:
    """Rank a symbol kind by overview relevance; unranked kinds sort last."""
    return _KIND_RANKS.get(str(kind), _UNRANKED_KIND)


def is_test_path(file_path: str) -> bool:
    """Report whether a path looks like test code (a ranking input, never confidence).

    Directory segments are split on dots so namespace-style folders
    (``Project.Tests``) count like plain ``tests`` directories.
    """
    parts = file_path.replace("\\", "/").split("/")
    for part in parts[:-1]:
        if any(piece in _TEST_DIRECTORY_SEGMENTS for piece in part.lower().split(".")):
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


def is_generated_path(file_path: str) -> bool:
    """Report whether a path looks generated: migrations, snapshots, designer files.

    Generic tooling conventions only — never repository-specific names. Like
    ``is_test_path`` this is a ranking/demotion input, never a confidence signal.
    """
    parts = file_path.replace("\\", "/").split("/")
    for part in parts[:-1]:
        if any(piece in _GENERATED_DIRECTORY_SEGMENTS for piece in part.lower().split(".")):
            return True
    file_name = parts[-1].lower()
    return any(marker in file_name for marker in _GENERATED_FILE_MARKERS)


def _demoted_evidence_path(file_path: str) -> bool:
    return is_test_path(file_path) or is_generated_path(file_path)


def path_segments(file_path: str) -> list[str]:
    """Return directory segments plus the file stem (``__init__`` dropped)."""
    parts = file_path.replace("\\", "/").split("/")
    stem = parts[-1].rsplit(".", 1)[0]
    segments = parts[:-1]
    if stem and stem != "__init__":
        segments = [*segments, stem]
    return segments


def import_segments(dotted_name: str) -> list[str]:
    """Split one imported dotted name into normalized segments."""
    normalized = dotted_name.replace("::", ".").replace("/", ".")
    return [segment for segment in normalized.split(".") if segment]


def import_matches_file(file_segments: list[str], dotted_name: str) -> bool:
    """Report whether a file's path segments end with an import's dotted segments.

    Declared module structure, never name-similarity guessing.
    """
    imported = import_segments(dotted_name)
    return (
        bool(imported)
        and len(imported) <= len(file_segments)
        and file_segments[-len(imported) :] == imported
    )


@dataclass(frozen=True, slots=True)
class MapAnchor:
    """One trusted central declaration inside an area."""

    symbol_id: str
    name: str
    kind: str
    file_path: str
    start_line: int
    trusted_in: int


@dataclass(frozen=True, slots=True)
class MapArea:
    """One bounded repository area (a directory subtree in the partition)."""

    path: str
    files: int
    symbols: int
    is_tests: bool
    anchors: tuple[MapAnchor, ...]


@dataclass(frozen=True, slots=True)
class MapEntrypoint:
    """One likely entrypoint found by a generic naming convention."""

    symbol_id: str
    name: str
    kind: str
    file_path: str
    start_line: int
    signal: str


@dataclass(frozen=True, slots=True)
class MapBridge:
    """Aggregated trusted evidence connecting two areas."""

    from_area: str
    to_area: str
    references: int
    imports: int
    examples: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepoMap:
    """The bounded, deterministic repository map."""

    version: int
    areas: tuple[MapArea, ...]
    entrypoints: tuple[MapEntrypoint, ...]
    bridges: tuple[MapBridge, ...]


@dataclass(slots=True)
class _TrieNode:
    children: dict[str, "_TrieNode"]
    direct_files: int = 0
    total_files: int = 0


def _build_trie(file_paths: list[str]) -> _TrieNode:
    root = _TrieNode(children={})
    for file_path in file_paths:
        parts = file_path.replace("\\", "/").split("/")
        node = root
        node.total_files += 1
        for part in parts[:-1]:
            node = node.children.setdefault(part, _TrieNode(children={}))
            node.total_files += 1
        node.direct_files += 1
    return root


def _collapse(node: _TrieNode, path: str) -> tuple[_TrieNode, str]:
    """Descend through pure pass-through directories (no direct files, one child)."""
    while node.direct_files == 0:
        populated = [(name, child) for name, child in node.children.items() if child.total_files]
        if len(populated) != 1:
            break
        name, child = populated[0]
        path = f"{path}/{name}" if path else name
        node = child
    return node, path


@dataclass(slots=True)
class _Area:
    path: str
    node: _TrieNode
    splittable: bool


def _split_candidates(area: _Area) -> list[tuple[str, _TrieNode]] | None:
    if not area.splittable:
        return None
    populated = sorted(
        (name, child) for name, child in area.node.children.items() if child.total_files
    )
    pseudo_children = len(populated) + (1 if area.node.direct_files else 0)
    if pseudo_children < 2:
        return None
    return populated


def _area_partition(file_paths: list[str]) -> list[_Area]:
    """Partition the file tree into bounded, size-proportional directory areas.

    Deterministic: always split the largest splittable area (ties by path), always
    collapse single-child directory chains, and stop at a target of roughly one
    area per ``AREA_TARGET_DIVISOR`` files (never more than ``MAX_AREAS``). A
    directory with more populated children than the remaining capacity splits
    partially: the largest children become areas and the parent stays as the
    residual area covering its direct files and the unsplit remainder.
    """
    if not file_paths:
        return []
    target_areas = min(MAX_AREAS, max(MIN_TARGET_AREAS, -(-len(file_paths) // AREA_TARGET_DIVISOR)))
    node, path = _collapse(_build_trie(sorted(file_paths)), "")
    areas = [_Area(path=path, node=node, splittable=True)]
    while len(areas) < target_areas:
        split_target: _Area | None = None
        split_children: list[tuple[str, _TrieNode]] = []
        for area in sorted(areas, key=lambda item: (-item.node.total_files, item.path)):
            children = _split_candidates(area)
            if children is not None:
                split_target, split_children = area, children
                break
        if split_target is None:
            break
        # Removing the parent frees one slot; a residual parent consumes it back.
        capacity = target_areas - (len(areas) - 1)
        needs_residual = bool(split_target.node.direct_files) or len(split_children) > capacity
        child_slots = capacity - 1 if needs_residual else capacity
        if child_slots < 1:
            break
        ranked = sorted(split_children, key=lambda entry: (-entry[1].total_files, entry[0]))
        selected = sorted(ranked[:child_slots])
        selected_names = {name for name, _ in selected}
        unsplit_remains = any(name not in selected_names for name, _ in split_children)
        areas.remove(split_target)
        if split_target.node.direct_files or unsplit_remains:
            areas.append(_Area(path=split_target.path, node=split_target.node, splittable=False))
        for name, child in selected:
            child_path = f"{split_target.path}/{name}" if split_target.path else name
            collapsed, collapsed_path = _collapse(child, child_path)
            areas.append(_Area(path=collapsed_path, node=collapsed, splittable=True))
    return sorted(areas, key=lambda item: item.path)


def area_resolver(area_paths: list[str]) -> "AreaResolver":
    """Build a longest-prefix resolver mapping file paths onto area paths.

    Deeper areas match first; the root area ("") has zero segments and is the
    catch-all for files no named area covers.
    """
    return AreaResolver(
        sorted(area_paths, key=lambda path: (-len(path.split("/")) if path else 0, path))
    )


@dataclass(frozen=True, slots=True)
class AreaResolver:
    """Resolve a file path to the deepest matching area path."""

    ordered_paths: list[str]

    def resolve(self, file_path: str) -> str | None:
        normalized = file_path.replace("\\", "/")
        for area_path in self.ordered_paths:
            if not area_path or normalized.startswith(area_path + "/"):
                return area_path
        return None


def _area_is_tests(area_path: str) -> bool:
    probe = f"{area_path}/x" if area_path else "x"
    return is_test_path(probe)


def area_is_demoted_evidence(area_path: str) -> bool:
    """Report whether an area path is test or generated territory (a demotion input)."""
    probe = f"{area_path}/x" if area_path else "x"
    return is_test_path(probe) or is_generated_path(probe)


def _anchor_sort_key(entry: tuple[int, Symbol]) -> tuple[int, int, int, str, int, str]:
    # Test and generated declarations anchor an area only after production ones.
    trusted_in, symbol = entry
    return (
        1 if _demoted_evidence_path(symbol.file_path) else 0,
        -trusted_in,
        kind_rank(symbol.kind),
        symbol.file_path,
        symbol.start_line,
        symbol.id,
    )


def _collect_anchors(reads: ReadProjections, resolver: AreaResolver) -> dict[str, list[MapAnchor]]:
    by_area: dict[str, list[tuple[int, Symbol]]] = {}
    for symbol, count in reads.trusted_incoming_degrees(TRUSTED_DEGREE_LIMIT):
        if str(symbol.kind) not in _KIND_RANKS:
            continue
        area = resolver.resolve(symbol.file_path)
        if area is not None:
            by_area.setdefault(area, []).append((count, symbol))
    anchors: dict[str, list[MapAnchor]] = {}
    for area, entries in by_area.items():
        entries.sort(key=_anchor_sort_key)
        anchors[area] = [
            MapAnchor(
                symbol_id=symbol.id,
                name=symbol.name,
                kind=str(symbol.kind),
                file_path=symbol.file_path,
                start_line=symbol.start_line,
                trusted_in=count,
            )
            for count, symbol in entries[:MAX_ANCHORS_PER_AREA]
        ]
    return anchors


def _fallback_anchors(
    reads: ReadProjections,
    resolver: AreaResolver,
    empty_areas: list[str],
) -> dict[str, list[MapAnchor]]:
    """Anchor areas without trusted references by containment and declaration rank."""
    child_counts = reads.containment_child_counts(1000)
    pool: dict[str, Symbol] = {}
    for symbol in reads.get_symbols_by_ids(sorted(child_counts)).values():
        pool.setdefault(symbol.id, symbol)
    for symbol in reads.top_declared_symbols(FALLBACK_ANCHOR_POOL_LIMIT):
        pool.setdefault(symbol.id, symbol)
    wanted = set(empty_areas)
    by_area: dict[str, list[Symbol]] = {}
    for symbol in pool.values():
        if str(symbol.kind) not in _KIND_RANKS:
            continue
        area = resolver.resolve(symbol.file_path)
        if area in wanted:
            assert area is not None
            by_area.setdefault(area, []).append(symbol)
    anchors: dict[str, list[MapAnchor]] = {}
    for area, symbols in by_area.items():
        symbols.sort(
            key=lambda symbol: (
                1 if _demoted_evidence_path(symbol.file_path) else 0,
                -min(child_counts.get(symbol.id, 0), 10),
                kind_rank(symbol.kind),
                symbol.file_path,
                symbol.start_line,
                symbol.id,
            )
        )
        anchors[area] = [
            MapAnchor(
                symbol_id=symbol.id,
                name=symbol.name,
                kind=str(symbol.kind),
                file_path=symbol.file_path,
                start_line=symbol.start_line,
                trusted_in=0,
            )
            for symbol in symbols[:MAX_ANCHORS_PER_AREA]
        ]
    return anchors


def _entrypoint_candidates(reads: ReadProjections, file_paths: list[str]) -> list[MapEntrypoint]:
    picked: dict[str, MapEntrypoint] = {}
    ranked: list[tuple[tuple[int, int, int, str, int, str], MapEntrypoint]] = []

    def add(symbol: Symbol, signal: str, signal_rank: int) -> None:
        if symbol.id in picked:
            return
        entry = MapEntrypoint(
            symbol_id=symbol.id,
            name=symbol.name,
            kind=str(symbol.kind),
            file_path=symbol.file_path,
            start_line=symbol.start_line,
            signal=signal,
        )
        picked[symbol.id] = entry
        depth = symbol.file_path.replace("\\", "/").count("/")
        ranked.append(
            (
                (
                    signal_rank,
                    kind_rank(symbol.kind),
                    depth,
                    symbol.file_path,
                    symbol.start_line,
                    symbol.id,
                ),
                entry,
            )
        )

    for base_name in ENTRYPOINT_SYMBOL_NAMES:
        for name in (base_name, base_name.capitalize()):
            for symbol in reads.get_definition(name):
                if symbol.kind in _ENTRYPOINT_CALLABLE_KINDS and not is_test_path(symbol.file_path):
                    add(symbol, "name-convention", 0)

    stem_files = [
        file_path
        for file_path in file_paths
        if file_path.replace("\\", "/").split("/")[-1].split(".", 1)[0].lower()
        in ENTRYPOINT_FILE_STEMS
        and not is_test_path(file_path)
    ]
    stem_files.sort(key=lambda path: (path.replace("\\", "/").count("/"), path))
    for file_path in stem_files[:ENTRYPOINT_FILE_CANDIDATES]:
        declarations = [
            symbol
            for symbol in reads.list_symbols_for_file(file_path)
            if str(symbol.kind) in _KIND_RANKS
        ]
        if declarations:
            add(declarations[0], "file-convention", 1)
        else:
            # Entry files built from top-level statements (a C# minimal-API
            # Program.cs, a script) declare nothing rankable; the file itself is
            # still the orientation evidence.
            file_name = file_path.replace("\\", "/").split("/")[-1]
            entry = MapEntrypoint(
                symbol_id="",
                name=file_name,
                kind="file",
                file_path=file_path,
                start_line=1,
                signal="file-convention",
            )
            key = f"file:{file_path}"
            if key not in picked:
                picked[key] = entry
                depth = file_path.replace("\\", "/").count("/")
                ranked.append(((1, _UNRANKED_KIND, depth, file_path, 1, key), entry))

    ranked.sort(key=lambda item: item[0])
    return [entry for _, entry in ranked[:MAX_ENTRYPOINTS]]


def _collect_bridges(
    reads: ReadProjections,
    resolver: AreaResolver,
    file_paths: list[str],
) -> list[MapBridge]:
    reference_counts: dict[tuple[str, str], int] = {}
    best_file_pair: dict[tuple[str, str], tuple[int, str, str]] = {}
    for from_file, to_file, count in reads.cross_file_reference_counts():
        from_area = resolver.resolve(from_file)
        to_area = resolver.resolve(to_file)
        if from_area is None or to_area is None or from_area == to_area:
            continue
        pair = (from_area, to_area)
        reference_counts[pair] = reference_counts.get(pair, 0) + count
        candidate = (-count, from_file, to_file)
        if pair not in best_file_pair or candidate < best_file_pair[pair]:
            best_file_pair[pair] = candidate

    import_counts: dict[tuple[str, str], int] = {}
    segments_by_file = {file_path: path_segments(file_path) for file_path in file_paths}
    files_by_last_segment: dict[str, list[str]] = {}
    for file_path, segments in segments_by_file.items():
        if segments:
            files_by_last_segment.setdefault(segments[-1], []).append(file_path)
    for from_file, dotted_name in reads.import_edges():
        from_area = resolver.resolve(from_file)
        if from_area is None:
            continue
        imported = import_segments(dotted_name)
        if not imported:
            continue
        for target_file in files_by_last_segment.get(imported[-1], ()):
            if not import_matches_file(segments_by_file[target_file], dotted_name):
                continue
            to_area = resolver.resolve(target_file)
            if to_area is None or to_area == from_area:
                continue
            pair = (from_area, to_area)
            import_counts[pair] = import_counts.get(pair, 0) + 1

    # Production bridges first: a bridge touching test or generated territory is
    # secondary orientation evidence and drops first under budget pressure.
    pairs = sorted(
        set(reference_counts) | set(import_counts),
        key=lambda pair: (
            1 if area_is_demoted_evidence(pair[0]) or area_is_demoted_evidence(pair[1]) else 0,
            -reference_counts.get(pair, 0),
            -import_counts.get(pair, 0),
            pair[0],
            pair[1],
        ),
    )
    bridges: list[MapBridge] = []
    for pair in pairs[:MAX_BRIDGES]:
        examples: tuple[str, ...] = ()
        best = best_file_pair.get(pair)
        if best is not None:
            _, from_file, to_file = best
            sites = reads.cross_file_reference_sites(from_file, to_file, MAX_BRIDGE_EXAMPLES)
            examples = tuple(f"{site_file}:{line}" for site_file, line in sites)
        bridges.append(
            MapBridge(
                from_area=pair[0],
                to_area=pair[1],
                references=reference_counts.get(pair, 0),
                imports=import_counts.get(pair, 0),
                examples=examples,
            )
        )
    return bridges


def compute_repo_map(reads: ReadProjections) -> RepoMap:
    """Derive the bounded repository map from one consistent index snapshot."""
    file_paths = sorted(source_file.path for source_file in reads.list_indexed_files())
    partition = _area_partition(file_paths)
    if not partition:
        return RepoMap(version=REPO_MAP_DERIVATION_VERSION, areas=(), entrypoints=(), bridges=())
    resolver = area_resolver([area.path for area in partition])
    symbol_counts = reads.symbol_counts_by_file()
    files_per_area: dict[str, int] = {}
    symbols_per_area: dict[str, int] = {}
    for file_path in file_paths:
        area = resolver.resolve(file_path)
        if area is None:
            continue
        files_per_area[area] = files_per_area.get(area, 0) + 1
        symbols_per_area[area] = symbols_per_area.get(area, 0) + symbol_counts.get(file_path, 0)

    anchors = _collect_anchors(reads, resolver)
    empty_areas = [area.path for area in partition if not anchors.get(area.path)]
    if empty_areas:
        anchors.update(_fallback_anchors(reads, resolver, empty_areas))

    areas = tuple(
        MapArea(
            path=area.path,
            files=files_per_area.get(area.path, 0),
            symbols=symbols_per_area.get(area.path, 0),
            is_tests=_area_is_tests(area.path),
            anchors=tuple(anchors.get(area.path, ())),
        )
        for area in sorted(
            partition,
            key=lambda item: (-files_per_area.get(item.path, 0), item.path),
        )
    )
    return RepoMap(
        version=REPO_MAP_DERIVATION_VERSION,
        areas=areas,
        entrypoints=tuple(_entrypoint_candidates(reads, file_paths)),
        bridges=tuple(_collect_bridges(reads, resolver, file_paths)),
    )


def serialize_repo_map(repo_map: RepoMap) -> str:
    """Serialize the map compactly with a stable key order."""
    payload = {
        "version": repo_map.version,
        "areas": [
            {
                "path": area.path,
                "files": area.files,
                "symbols": area.symbols,
                "tests": area.is_tests,
                "anchors": [
                    {
                        "id": anchor.symbol_id,
                        "name": anchor.name,
                        "kind": anchor.kind,
                        "file": anchor.file_path,
                        "line": anchor.start_line,
                        "refs": anchor.trusted_in,
                    }
                    for anchor in area.anchors
                ],
            }
            for area in repo_map.areas
        ],
        "entrypoints": [
            {
                "id": entry.symbol_id,
                "name": entry.name,
                "kind": entry.kind,
                "file": entry.file_path,
                "line": entry.start_line,
                "signal": entry.signal,
            }
            for entry in repo_map.entrypoints
        ],
        "bridges": [
            {
                "from": bridge.from_area,
                "to": bridge.to_area,
                "refs": bridge.references,
                "imports": bridge.imports,
                "at": list(bridge.examples),
            }
            for bridge in repo_map.bridges
        ],
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def parse_repo_map(raw: str) -> RepoMap | None:
    """Parse a stored map; None when malformed (never raises)."""
    try:
        payload = json.loads(raw)
        areas = tuple(
            MapArea(
                path=str(area["path"]),
                files=int(area["files"]),
                symbols=int(area["symbols"]),
                is_tests=bool(area["tests"]),
                anchors=tuple(
                    MapAnchor(
                        symbol_id=str(anchor["id"]),
                        name=str(anchor["name"]),
                        kind=str(anchor["kind"]),
                        file_path=str(anchor["file"]),
                        start_line=int(anchor["line"]),
                        trusted_in=int(anchor["refs"]),
                    )
                    for anchor in area["anchors"]
                ),
            )
            for area in payload["areas"]
        )
        entrypoints = tuple(
            MapEntrypoint(
                symbol_id=str(entry["id"]),
                name=str(entry["name"]),
                kind=str(entry["kind"]),
                file_path=str(entry["file"]),
                start_line=int(entry["line"]),
                signal=str(entry["signal"]),
            )
            for entry in payload["entrypoints"]
        )
        bridges = tuple(
            MapBridge(
                from_area=str(bridge["from"]),
                to_area=str(bridge["to"]),
                references=int(bridge["refs"]),
                imports=int(bridge["imports"]),
                examples=tuple(str(example) for example in bridge["at"]),
            )
            for bridge in payload["bridges"]
        )
        return RepoMap(
            version=int(payload["version"]),
            areas=areas,
            entrypoints=entrypoints,
            bridges=bridges,
        )
    except (ValueError, KeyError, TypeError):
        return None


def load_repo_map(reads: ReadProjections) -> RepoMap | None:
    """Load the stored map; None when absent, stale-versioned, or malformed."""
    if reads.get_meta(REPO_MAP_VERSION_KEY) != str(REPO_MAP_DERIVATION_VERSION):
        return None
    raw = reads.get_meta(REPO_MAP_KEY)
    if raw is None:
        return None
    repo_map = parse_repo_map(raw)
    if repo_map is None or repo_map.version != REPO_MAP_DERIVATION_VERSION:
        return None
    return repo_map


def refresh_repo_map(connection: sqlite3.Connection) -> None:
    """Recompute and store the map on the caller's write connection."""
    repo_map = compute_repo_map(ReadProjections(connection))
    set_meta(connection, REPO_MAP_KEY, serialize_repo_map(repo_map))
    set_meta(connection, REPO_MAP_VERSION_KEY, str(REPO_MAP_DERIVATION_VERSION))
