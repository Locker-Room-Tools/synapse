"""Tree-sitter parsing into normalized symbols."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from tree_sitter import Parser, Query, QueryCursor
from tree_sitter_language_pack import get_language

from synapse.core.languages import to_treesitter_name
from synapse.core.models import Confidence, Relation, RelationKind, Symbol, SymbolKind
from synapse.core.queries import load_query


@dataclass(slots=True)
class _ExtractedSymbol:
    language: str
    kind: SymbolKind
    native_kind: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    signature: str | None
    container_index: int | None = None
    qualified_name: str | None = None


@dataclass(frozen=True, slots=True)
class RawReference:
    name: str
    start_line: int
    start_byte: int
    from_symbol: Symbol


_CAPTURE_KIND_MAP = {
    "class": SymbolKind.CLASS,
    "constant": SymbolKind.CONSTANT,
    "constructor": SymbolKind.CONSTRUCTOR,
    "enum": SymbolKind.ENUM,
    "field": SymbolKind.FIELD,
    "function": SymbolKind.FUNCTION,
    "import": SymbolKind.IMPORT,
    "interface": SymbolKind.INTERFACE,
    "method": SymbolKind.METHOD,
    "module": SymbolKind.MODULE,
    "namespace": SymbolKind.NAMESPACE,
    "package": SymbolKind.PACKAGE,
    "property": SymbolKind.PROPERTY,
    "record": SymbolKind.RECORD,
    "struct": SymbolKind.STRUCT,
    "type": SymbolKind.TYPE,
    "variable": SymbolKind.VARIABLE,
}


def _capture_kind_to_symbol_kind(capture_name: str, symbol_name: str) -> SymbolKind:
    raw_kind = capture_name.removeprefix("definition.")
    try:
        symbol_kind = _CAPTURE_KIND_MAP[raw_kind]
    except KeyError as exc:
        msg = f"Unsupported symbol capture kind: {capture_name}"
        raise ValueError(msg) from exc
    if symbol_kind in {SymbolKind.FIELD, SymbolKind.VARIABLE} and symbol_name.isupper():
        return SymbolKind.CONSTANT
    return symbol_kind


def _decode_node_text(source_bytes: bytes, start_byte: int, end_byte: int) -> str:
    return source_bytes[start_byte:end_byte].decode("utf-8", errors="replace")


def _signature_for_range(source_bytes: bytes, start_byte: int, end_byte: int) -> str | None:
    snippet = _decode_node_text(source_bytes, start_byte, end_byte)
    first_line = snippet.splitlines()[0].strip() if snippet else ""
    return first_line or None


def _assign_containers(items: list[_ExtractedSymbol]) -> None:
    for index, item in enumerate(items):
        enclosing_indexes = [
            candidate_index
            for candidate_index, candidate in enumerate(items)
            if candidate_index != index
            and candidate.start_byte <= item.start_byte
            and candidate.end_byte >= item.end_byte
            and (candidate.start_byte < item.start_byte or candidate.end_byte > item.end_byte)
        ]
        if not enclosing_indexes:
            continue
        item.container_index = min(
            enclosing_indexes,
            key=lambda candidate_index: (
                items[candidate_index].end_byte - items[candidate_index].start_byte
            ),
        )


def _qualified_name(items: list[_ExtractedSymbol], index: int) -> str:
    item = items[index]
    if item.qualified_name is not None:
        return item.qualified_name
    if item.container_index is None:
        item.qualified_name = item.name
        return item.qualified_name
    item.qualified_name = f"{_qualified_name(items, item.container_index)}.{item.name}"
    return item.qualified_name


def _symbol_id(item: _ExtractedSymbol) -> str:
    identity_name = item.qualified_name or item.name
    return f"{item.language}:{item.file_path}:{item.kind}:{identity_name}:{item.start_line}"


def _stored_file_path(path: Path, workspace_root: Path | None) -> str:
    if workspace_root is not None:
        resolved_root = workspace_root.resolve()
        resolved_path = path.resolve()
        if resolved_path.is_relative_to(resolved_root):
            return resolved_path.relative_to(resolved_root).as_posix()
    return path.as_posix()


def _enclosing_symbol(symbols: Sequence[Symbol], start_byte: int) -> Symbol | None:
    candidates = [
        symbol
        for symbol in symbols
        if symbol.start_byte <= start_byte and symbol.end_byte >= start_byte
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda symbol: symbol.end_byte - symbol.start_byte)


def parse_file(path: Path, language: str, workspace_root: Path | None = None) -> list[Symbol]:
    """Parse a source file into normalized symbols."""
    tree_sitter_language = get_language(to_treesitter_name(language))
    parser = Parser(tree_sitter_language)
    source_bytes = path.read_bytes()
    tree = parser.parse(source_bytes)
    query = Query(tree_sitter_language, load_query(language, "symbols"))
    matches = QueryCursor(query).matches(tree.root_node)
    stored_file_path = _stored_file_path(path, workspace_root)

    extracted: list[_ExtractedSymbol] = []
    for _, captures in matches:
        definition_capture = next(
            (capture_name for capture_name in captures if capture_name.startswith("definition.")),
            None,
        )
        name_nodes = captures.get("name")
        if definition_capture is None or not name_nodes:
            continue
        definition_node = captures[definition_capture][0]
        name_node = name_nodes[0]
        symbol_name = _decode_node_text(source_bytes, name_node.start_byte, name_node.end_byte)
        kind = _capture_kind_to_symbol_kind(definition_capture, symbol_name)
        extracted.append(
            _ExtractedSymbol(
                language=language,
                kind=kind,
                native_kind=definition_node.type,
                name=symbol_name,
                file_path=stored_file_path,
                start_line=definition_node.start_point[0] + 1,
                end_line=definition_node.end_point[0] + 1,
                start_byte=definition_node.start_byte,
                end_byte=definition_node.end_byte,
                signature=_signature_for_range(
                    source_bytes,
                    definition_node.start_byte,
                    definition_node.end_byte,
                ),
            )
        )

    extracted.sort(
        key=lambda item: (item.start_byte, -(item.end_byte - item.start_byte), item.name)
    )
    _assign_containers(extracted)
    for index, _ in enumerate(extracted):
        _qualified_name(extracted, index)

    symbols = [
        Symbol(
            id=_symbol_id(item),
            language=item.language,
            kind=item.kind,
            native_kind=item.native_kind,
            name=item.name,
            qualified_name=item.qualified_name,
            file_path=item.file_path,
            container_id=None,
            start_line=item.start_line,
            end_line=item.end_line,
            start_byte=item.start_byte,
            end_byte=item.end_byte,
            signature=item.signature,
            source="tree-sitter",
            confidence=Confidence.HIGH,
        )
        for item in extracted
    ]
    for index, item in enumerate(extracted):
        if item.container_index is None:
            continue
        symbols[index] = replace(symbols[index], container_id=symbols[item.container_index].id)
    return symbols


def extract_references(path: Path, language: str, symbols: Sequence[Symbol]) -> list[RawReference]:
    """Extract raw symbol references from one source file."""
    try:
        query_text = load_query(language, "references")
    except FileNotFoundError:
        return []

    tree_sitter_language = get_language(to_treesitter_name(language))
    parser = Parser(tree_sitter_language)
    source_bytes = path.read_bytes()
    tree = parser.parse(source_bytes)
    query = Query(tree_sitter_language, query_text)
    matches = QueryCursor(query).matches(tree.root_node)

    raw_refs: list[RawReference] = []
    for _, captures in matches:
        reference_nodes = captures.get("reference", [])
        for node in reference_nodes:
            from_symbol = _enclosing_symbol(symbols, node.start_byte)
            if from_symbol is None:
                continue
            raw_refs.append(
                RawReference(
                    name=_decode_node_text(source_bytes, node.start_byte, node.end_byte),
                    start_line=node.start_point[0] + 1,
                    start_byte=node.start_byte,
                    from_symbol=from_symbol,
                )
            )
    return raw_refs


def _candidate_symbol_ids(name: str, name_to_symbol_ids: Mapping[str, list[str]]) -> list[str]:
    candidates: set[str] = set(name_to_symbol_ids.get(name, []))
    suffix = f".{name}"
    for indexed_name, symbol_ids in name_to_symbol_ids.items():
        if indexed_name.endswith(suffix):
            candidates.update(symbol_ids)
    return sorted(candidates)


def build_reference_relations(
    raw_refs: Sequence[RawReference],
    name_to_symbol_ids: Mapping[str, list[str]],
) -> list[Relation]:
    """Resolve raw references into deterministic reference relations."""
    relations: list[Relation] = []
    for raw_ref in raw_refs:
        candidates = _candidate_symbol_ids(raw_ref.name, name_to_symbol_ids)
        to_symbol_id: str | None = None
        confidence = Confidence.LOW
        if len(candidates) == 1:
            to_symbol_id = candidates[0]
            confidence = Confidence.HIGH
        elif len(candidates) > 1:
            confidence = Confidence.MEDIUM
        target = to_symbol_id or raw_ref.name
        relations.append(
            Relation(
                id=(
                    f"references:{raw_ref.from_symbol.id}:{target}:"
                    f"{raw_ref.start_line}:{raw_ref.start_byte}"
                ),
                kind=RelationKind.REFERENCES,
                from_symbol_id=raw_ref.from_symbol.id,
                to_symbol_id=to_symbol_id,
                from_file_path=raw_ref.from_symbol.file_path,
                to_file_path=None,
                to_name=raw_ref.name,
                source="tree-sitter",
                confidence=confidence,
            )
        )
    return relations


def build_relations(symbols: Sequence[Symbol]) -> list[Relation]:
    """Derive deterministic structural relations from extracted symbols."""
    relations: list[Relation] = []
    for symbol in symbols:
        if symbol.container_id is not None:
            relations.append(
                Relation(
                    id=f"contains:{symbol.container_id}:{symbol.id}",
                    kind=RelationKind.CONTAINS,
                    from_symbol_id=symbol.container_id,
                    to_symbol_id=symbol.id,
                    from_file_path=symbol.file_path,
                    to_file_path=symbol.file_path,
                    to_name=symbol.name,
                    source="tree-sitter",
                    confidence=Confidence.HIGH,
                )
            )
        if symbol.kind is SymbolKind.IMPORT:
            relations.append(
                Relation(
                    id=f"imports:{symbol.id}",
                    kind=RelationKind.IMPORTS,
                    from_symbol_id=symbol.id,
                    to_symbol_id=None,
                    from_file_path=symbol.file_path,
                    to_file_path=None,
                    to_name=symbol.name,
                    source="tree-sitter",
                    confidence=Confidence.HIGH,
                )
            )
    return relations
