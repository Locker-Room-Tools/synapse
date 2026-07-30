"""Tests for deterministic seed discovery and ranking."""

from pathlib import Path

from synapse.core.context import QueryKeywords, discover_seeds
from synapse.core.context.seeds import SeedMatch, is_test_path
from synapse.core.index import SymbolIndex
from synapse.core.models import Confidence, SourceFile, Symbol, SymbolKind


def _symbol(
    symbol_id: str,
    name: str,
    file_path: str,
    *,
    kind: SymbolKind = SymbolKind.FUNCTION,
    start_line: int = 1,
) -> Symbol:
    return Symbol(
        id=symbol_id,
        language="python",
        kind=kind,
        native_kind="test",
        name=name,
        qualified_name=name,
        file_path=file_path,
        container_id=None,
        start_line=start_line,
        end_line=start_line + 1,
        start_byte=start_line * 100,
        end_byte=start_line * 100 + 10,
        signature=None,
        source="test",
        confidence=Confidence.HIGH,
    )


def _build_index(tmp_path: Path, symbols_by_file: dict[str, list[Symbol]]) -> SymbolIndex:
    index = SymbolIndex(tmp_path / "index.sqlite")
    for file_path, symbols in symbols_by_file.items():
        index.upsert_file(
            SourceFile(
                id=file_path,
                path=file_path,
                language="python",
                project_root=None,
                content_hash=f"hash-{file_path}",
                indexed_at="2026-07-30T00:00:00+00:00",
            )
        )
        index.replace_symbols_for_file(file_path, symbols, [])
    return index


def test_exact_name_outranks_prefix_matches(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        {
            "src/worker.py": [
                _symbol("sym-1", "WatchWorker", "src/worker.py", kind=SymbolKind.CLASS),
                _symbol(
                    "sym-2",
                    "WatchWorkerFactory",
                    "src/worker.py",
                    kind=SymbolKind.CLASS,
                    start_line=10,
                ),
            ]
        },
    )
    keywords = QueryKeywords(identifiers=("WatchWorker",), terms=("watch", "worker"))
    with index.read_session() as reads:
        discovery = discover_seeds(reads, keywords)
    assert [seed.symbol.name for seed in discovery.seeds] == [
        "WatchWorker",
        "WatchWorkerFactory",
    ]
    assert discovery.seeds[0].match is SeedMatch.EXACT_NAME
    assert discovery.seeds[1].match is SeedMatch.PREFIX


def test_production_code_outranks_test_paths(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        {
            "tests/test_worker.py": [
                _symbol("sym-fake", "Worker", "tests/test_worker.py", kind=SymbolKind.CLASS)
            ],
            "src/worker.py": [
                _symbol("sym-real", "Worker", "src/worker.py", kind=SymbolKind.CLASS)
            ],
        },
    )
    keywords = QueryKeywords(identifiers=("Worker",), terms=("worker",))
    with index.read_session() as reads:
        discovery = discover_seeds(reads, keywords)
    assert [seed.symbol.id for seed in discovery.seeds] == ["sym-real", "sym-fake"]
    assert is_test_path(discovery.seeds[1].symbol.file_path)


def test_ambiguity_is_reported_through_alternates(tmp_path: Path) -> None:
    symbols = [
        _symbol(f"sym-{i:02d}", f"Handler{i:02d}", "src/handlers.py", start_line=i * 5 + 1)
        for i in range(12)
    ]
    index = _build_index(tmp_path, {"src/handlers.py": symbols})
    keywords = QueryKeywords(identifiers=(), terms=("handler",))
    with index.read_session() as reads:
        discovery = discover_seeds(reads, keywords)
    assert len(discovery.seeds) == 5
    assert len(discovery.alternates) == 7
    assert discovery.total_candidates == 12


def test_explicit_ids_skip_keyword_discovery_and_report_unknown(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        {
            "src/a.py": [_symbol("sym-a", "alpha", "src/a.py")],
            "src/b.py": [_symbol("sym-b", "beta", "src/b.py")],
        },
    )
    keywords = QueryKeywords(identifiers=("beta",), terms=("beta",))
    with index.read_session() as reads:
        discovery = discover_seeds(reads, keywords, ("sym-a", "sym-missing"))
    assert [seed.symbol.id for seed in discovery.seeds] == ["sym-a"]
    assert discovery.seeds[0].match is SeedMatch.EXPLICIT
    assert discovery.unknown_symbol_ids == ("sym-missing",)


def test_all_unknown_explicit_ids_fall_back_to_keywords(tmp_path: Path) -> None:
    index = _build_index(tmp_path, {"src/a.py": [_symbol("sym-a", "alpha", "src/a.py")]})
    keywords = QueryKeywords(identifiers=("alpha",), terms=("alpha",))
    with index.read_session() as reads:
        discovery = discover_seeds(reads, keywords, ("sym-gone",))
    assert [seed.symbol.id for seed in discovery.seeds] == ["sym-a"]
    assert discovery.unknown_symbol_ids == ("sym-gone",)


def test_terms_are_used_only_when_identifiers_find_nothing(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path, {"src/daemon.py": [_symbol("sym-d", "daemon_loop", "src/daemon.py")]}
    )
    keywords = QueryKeywords(identifiers=("NoSuchSymbol",), terms=("daemon",))
    with index.read_session() as reads:
        discovery = discover_seeds(reads, keywords)
    assert [seed.symbol.id for seed in discovery.seeds] == ["sym-d"]
    assert discovery.seeds[0].match is SeedMatch.TERM


def test_discovery_is_deterministic(tmp_path: Path) -> None:
    symbols = [
        _symbol(f"sym-{i}", f"Service{i}", "src/services.py", start_line=i * 3 + 1)
        for i in range(8)
    ]
    index = _build_index(tmp_path, {"src/services.py": symbols})
    keywords = QueryKeywords(identifiers=("Service1",), terms=("service",))
    with index.read_session() as reads:
        first = discover_seeds(reads, keywords)
        second = discover_seeds(reads, keywords)
    assert first == second


def test_symbols_matching_more_query_terms_rank_higher(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        {
            "src/watch.py": [
                _symbol("sym-one-term", "watcher", "src/watch.py"),
                _symbol("sym-two-terms", "watch_daemon_loop", "src/watch.py", start_line=10),
            ]
        },
    )
    keywords = QueryKeywords(identifiers=(), terms=("watch", "daemon"))
    with index.read_session() as reads:
        discovery = discover_seeds(reads, keywords)
    assert discovery.seeds[0].symbol.id == "sym-two-terms"
