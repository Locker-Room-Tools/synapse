"""Tree-sitter parsing into normalized symbols."""

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from tree_sitter import Language, Node, Parser, Query, QueryCursor, Tree

from synapse.core.indexing.resolution import (
    ResolutionFacts,
    ResolvedReference,
    resolve_reference,
)
from synapse.core.languages import (
    ReferenceSyntax,
    name_separator,
    to_treesitter_name,
    uses_uppercase_constants,
)
from synapse.core.languages import (
    file_scoped_container_types as get_file_scoped_container_types,
)
from synapse.core.languages import reference_syntax as get_reference_syntax
from synapse.core.languages.grammars import get_installed_language
from synapse.core.languages.queries import load_query
from synapse.core.models import (
    Confidence,
    Relation,
    RelationKind,
    ResolutionMethod,
    Symbol,
    SymbolKind,
)

# Bump whenever Python-side reference-extraction or resolution semantics change;
# feeds the index-content fingerprint that invalidates stale relation rows.
REFERENCE_EXTRACTOR_VERSION = 3


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
class TypeBinding:
    """One name bound to a syntactically declared type over a byte range."""

    name: str
    type_name: str
    scope_start_byte: int
    scope_end_byte: int


@dataclass(frozen=True, slots=True)
class FileScope:
    """Per-file syntactic facts a structural resolver may rely on.

    Everything here is read directly off the syntax tree. Nothing is inferred: a
    `var` local or an implicitly typed lambda parameter contributes no binding, which
    is what keeps such receivers honestly ambiguous.
    """

    namespaces: tuple[tuple[str, int, int], ...] = ()
    imported_namespaces: tuple[str, ...] = ()
    aliases: tuple[tuple[str, str], ...] = ()
    bindings: tuple[TypeBinding, ...] = ()
    # Same-file callables with an explicit, resolvable return annotation, so a
    # factory-call receiver can be typed. Conflicting duplicate names are dropped.
    return_types: tuple[tuple[str, str], ...] = ()
    # Receiver spellings denoting the enclosing type (from the language syntax);
    # carried per file so the reconcile path needs no extra wiring.
    self_receivers: tuple[str, ...] = ()

    def return_type_of(self, callable_name: str) -> str | None:
        """Return the explicit annotated return type of a same-file callable."""
        for name, type_name in self.return_types:
            if name == callable_name:
                return type_name
        return None

    def namespace_at(self, start_byte: int) -> str | None:
        """Return the innermost namespace covering a byte offset."""
        covering = [
            (name, end - start)
            for name, start, end in self.namespaces
            if start <= start_byte <= end
        ]
        if not covering:
            return None
        return min(covering, key=lambda entry: entry[1])[0]

    def declared_type_at(self, name: str, start_byte: int) -> str | None:
        """Return the declared type of `name` in the innermost scope covering an offset."""
        covering = [
            binding
            for binding in self.bindings
            if binding.name == name
            and binding.scope_start_byte <= start_byte <= binding.scope_end_byte
        ]
        if not covering:
            return None
        # Inner scopes shadow outer ones; this is language semantics, not proximity.
        innermost = min(
            covering,
            key=lambda binding: binding.scope_end_byte - binding.scope_start_byte,
        )
        distinct = {
            binding.type_name
            for binding in covering
            if binding.scope_end_byte - binding.scope_start_byte
            == innermost.scope_end_byte - innermost.scope_start_byte
        }
        return innermost.type_name if len(distinct) == 1 else None


@dataclass(frozen=True, slots=True)
class RawReference:
    name: str
    start_line: int
    # 1-based byte offset within the line (tree-sitter column).
    start_byte_col: int
    start_byte: int
    file_path: str
    language: str
    # None for usages outside any indexed declaration (C# top-level statements).
    from_symbol: Symbol | None = None
    usage_kind: str | None = None
    # Full dotted name the identifier belongs to, e.g. `Overlock.Api.Servers.Server`.
    qualified_text: str | None = None
    # Receiver of a member access, e.g. `dbContext` in `dbContext.Servers`.
    receiver_text: str | None = None
    # When the receiver is itself a call, the called function's dotted name
    # (`factory` in `factory(...).method()`); receiver_text stays None then.
    receiver_call: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedSource:
    """Symbols and raw references extracted from one parsed syntax tree."""

    symbols: list[Symbol]
    references: list[RawReference]
    scope: FileScope = FileScope()


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


def _capture_kind_to_symbol_kind(
    capture_name: str,
    symbol_name: str,
    *,
    uppercase_constants: bool,
) -> SymbolKind:
    raw_kind = capture_name.removeprefix("definition.")
    try:
        symbol_kind = _CAPTURE_KIND_MAP[raw_kind]
    except KeyError as exc:
        msg = f"Unsupported symbol capture kind: {capture_name}"
        raise ValueError(msg) from exc
    if (
        uppercase_constants
        and symbol_kind in {SymbolKind.FIELD, SymbolKind.VARIABLE}
        and symbol_name.isupper()
    ):
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


def _qualified_name(items: list[_ExtractedSymbol], index: int, separator: str = ".") -> str:
    item = items[index]
    if item.qualified_name is not None:
        return item.qualified_name
    if item.container_index is None:
        item.qualified_name = item.name
        return item.qualified_name
    container_name = _qualified_name(items, item.container_index, separator)
    item.qualified_name = f"{container_name}{separator}{item.name}"
    return item.qualified_name


def _symbol_id(item: _ExtractedSymbol) -> str:
    identity_name = item.qualified_name or item.name
    return f"{item.language}:{item.file_path}:{item.kind}:{identity_name}:{item.start_line}"


def _stored_file_path(path: Path, workspace_root: Path | None) -> str:
    if workspace_root is not None:
        absolute_root = workspace_root.absolute()
        absolute_path = path.absolute()
        if absolute_path.is_relative_to(absolute_root):
            return absolute_path.relative_to(absolute_root).as_posix()
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


def _extract_symbols_from_tree(
    path: Path,
    language: str,
    source_bytes: bytes,
    tree_sitter_language: Language,
    tree: Tree,
    workspace_root: Path | None,
) -> list[Symbol]:
    query = Query(tree_sitter_language, load_query(language, "symbols"))
    matches = QueryCursor(query).matches(tree.root_node)
    stored_file_path = _stored_file_path(path, workspace_root)
    separator = name_separator(language)
    uppercase_constants = uses_uppercase_constants(language)
    file_scoped_types = get_file_scoped_container_types(language)

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
        kind = _capture_kind_to_symbol_kind(
            definition_capture,
            symbol_name,
            uppercase_constants=uppercase_constants,
        )
        # A file-scoped declaration (C# `namespace X;`) syntactically ends at its
        # semicolon but scopes everything after it, so widen it to its parent. Without
        # this, nothing nests inside it and qualified names lose their namespace.
        end_node = definition_node
        if definition_node.type in file_scoped_types and definition_node.parent is not None:
            end_node = definition_node.parent
        extracted.append(
            _ExtractedSymbol(
                language=language,
                kind=kind,
                native_kind=definition_node.type,
                name=symbol_name,
                file_path=stored_file_path,
                start_line=definition_node.start_point[0] + 1,
                end_line=end_node.end_point[0] + 1,
                start_byte=definition_node.start_byte,
                end_byte=end_node.end_byte,
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
        _qualified_name(extracted, index, separator)

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


_REFERENCE_CAPTURE_PREFIX = "reference"

# When several patterns match one identifier, the most specific usage kind wins. One
# source location stays exactly one usage; only its label is decided here.
_USAGE_KIND_PRIORITY: tuple[str, ...] = (
    "nameof",
    "object-creation",
    "base-type",
    "attribute",
    "type-literal",
    "cast-and-pattern",
    "return-type",
    "declared-type",
    "type-argument",
    "generic-type",
    "member-access",
    "invocation",
)


def _usage_kind_for_capture(capture_name: str) -> str | None:
    """Map a `reference.<suffix>` capture to a usage-kind id, or None if unlabelled.

    A bare `@reference` capture carries no positional information, so languages that
    advertise no usage kinds report none rather than a placeholder.
    """
    _, _, suffix = capture_name.partition(".")
    return suffix.replace("_", "-") if suffix else None


def _usage_kind_rank(usage_kind: str | None) -> int:
    if usage_kind is None:
        return len(_USAGE_KIND_PRIORITY)
    try:
        return _USAGE_KIND_PRIORITY.index(usage_kind)
    except ValueError:
        return len(_USAGE_KIND_PRIORITY)


def _field_child(node: Node, field: str) -> Node | None:
    return node.child_by_field_name(field)


def _is_same_node(left: Node | None, right: Node | None) -> bool:
    """Compare two nodes by identity of the underlying tree position.

    tree-sitter returns a fresh wrapper object per lookup, so `is` never matches.
    """
    if left is None or right is None:
        return False
    return left.id == right.id


def _node_text(source_bytes: bytes, node: Node) -> str:
    return _decode_node_text(source_bytes, node.start_byte, node.end_byte)


def _dotted_name(source_bytes: bytes, node: Node, syntax: ReferenceSyntax) -> str:
    """Return a node's dotted text with any alias qualifier reduced to its name side."""
    if node.type in syntax.alias_qualified_types:
        name_node = _field_child(node, syntax.name_field)
        return _node_text(source_bytes, name_node) if name_node is not None else ""
    if node.type in syntax.qualified_types:
        qualifier = _field_child(node, syntax.qualifier_field)
        name_node = _field_child(node, syntax.name_field)
        if qualifier is None or name_node is None:
            return _node_text(source_bytes, node)
        left = _dotted_name(source_bytes, qualifier, syntax)
        right = _node_text(source_bytes, name_node)
        return f"{left}.{right}" if left else right
    return _node_text(source_bytes, node)


_DOTTED_PATH = re.compile(r"[^\W\d]\w*(?:\.[^\W\d]\w*)*\Z", re.UNICODE)


def _qualified_text(source_bytes: bytes, node: Node, syntax: ReferenceSyntax) -> str | None:
    """Return the full dotted name a captured trailing identifier belongs to."""
    current = node
    parent = current.parent
    outermost: Node | None = None
    while parent is not None and parent.type in (
        *syntax.qualified_types,
        *syntax.alias_qualified_types,
    ):
        if not _is_same_node(_field_child(parent, syntax.name_field), current):
            break
        outermost = parent
        current = parent
        parent = current.parent
    if outermost is not None:
        return _dotted_name(source_bytes, outermost, syntax) or None

    # Expression positions spell dotted paths as nested member accesses rather than
    # qualified names. Only a pure `a.b.C` path counts; anything with a call, index,
    # or literal in the chain proves nothing about a declaration's qualified name.
    if parent is not None and parent.type in syntax.member_access_types:
        if not _is_same_node(_field_child(parent, _member_name_field(syntax)), node):
            return None
        chain = _node_text(source_bytes, parent)
        if _DOTTED_PATH.match(chain):
            return chain
    return None


def _member_name_field(syntax: ReferenceSyntax) -> str:
    return syntax.member_name_field or syntax.name_field


def _receiver_parts(
    source_bytes: bytes, node: Node, syntax: ReferenceSyntax
) -> tuple[str | None, str | None]:
    """Return (receiver text, receiver-call name) for a member access at this identifier.

    A plain receiver expression yields its source text. A call receiver
    (`factory(...).method()`) yields the called function's dotted name instead —
    arguments prove nothing and are never part of receiver evidence.
    """
    parent = node.parent
    if parent is None or parent.type not in syntax.member_access_types:
        return None, None
    if not _is_same_node(_field_child(parent, _member_name_field(syntax)), node):
        return None, None
    receiver = _field_child(parent, syntax.receiver_field)
    if receiver is None:
        return None, None
    if receiver.type in syntax.call_types:
        function_node = _field_child(receiver, syntax.call_function_field)
        if function_node is None:
            return None, None
        function_text = _node_text(source_bytes, function_node)
        if _DOTTED_PATH.match(function_text):
            return None, function_text
        return None, None
    return _node_text(source_bytes, receiver), None


def _base_type_name(source_bytes: bytes, node: Node, syntax: ReferenceSyntax) -> str | None:
    """Reduce a type expression to the simple name of the declaration it names."""
    if node.type in syntax.opaque_type_types:
        return None
    if node.type in syntax.type_wrapper_types:
        inner = _field_child(node, syntax.type_field)
        if inner is None:
            # Python's `type` wrapper holds its annotation as an unnamed child.
            inner = next(iter(node.named_children), None)
        return _base_type_name(source_bytes, inner, syntax) if inner is not None else None
    if node.type in syntax.generic_types:
        for child in node.children:
            if child.type == "identifier":
                return _node_text(source_bytes, child)
        return None
    if node.type in syntax.dotted_type_types:
        text = _node_text(source_bytes, node)
        if _DOTTED_PATH.match(text):
            return text.split(".")[-1]
        return None
    if node.type in syntax.qualified_types or node.type in syntax.alias_qualified_types:
        name_node = _field_child(node, syntax.name_field)
        return _node_text(source_bytes, name_node) if name_node is not None else None
    if node.type == "identifier":
        return _node_text(source_bytes, node)
    return None


def _constructor_call_type(source_bytes: bytes, node: Node, syntax: ReferenceSyntax) -> str | None:
    """Return the called name when a binder's value is a direct call (`x = Foo(...)`).

    This records the call target as a *candidate* type name only; the resolver's
    unique-type-kind gate decides whether it actually names a type, so binding to a
    factory function or an unknown name proves nothing and stays ambiguous.
    """
    for value_field in syntax.binder_value_fields:
        value = _field_child(node, value_field)
        if value is None or value.type not in syntax.call_types:
            continue
        function_node = _field_child(value, syntax.call_function_field)
        if function_node is None:
            continue
        function_text = _node_text(source_bytes, function_node)
        if _DOTTED_PATH.match(function_text):
            return function_text.split(".")[-1]
    return None


def _binder_scope(node: Node, syntax: ReferenceSyntax) -> tuple[int, int] | None:
    """Return the byte range of the nearest scope-defining ancestor of a binder."""
    current = node.parent
    while current is not None:
        if current.type in syntax.scope_types:
            return current.start_byte, current.end_byte
        current = current.parent
    return None


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _build_file_scope(
    language: str,
    source_bytes: bytes,
    tree: Tree,
) -> FileScope:
    """Collect namespaces, imports, and declared-type bindings from one syntax tree."""
    syntax = get_reference_syntax(language)
    if syntax is None:
        return FileScope()

    namespaces: list[tuple[str, int, int]] = []
    imported: list[str] = []
    aliases: list[tuple[str, str]] = []
    bindings: list[TypeBinding] = []
    return_types: dict[str, str] = {}
    conflicting_returns: set[str] = set()

    for node in _walk(tree.root_node):
        if node.type in syntax.namespace_types:
            name_node = _field_child(node, syntax.name_field)
            if name_node is not None:
                namespaces.append(
                    (
                        _dotted_name(source_bytes, name_node, syntax),
                        node.start_byte,
                        node.end_byte,
                    )
                )
            continue
        if node.type in syntax.import_types:
            alias_node = _field_child(node, syntax.name_field)
            target = next(
                (
                    child
                    for child in reversed(node.children)
                    if child.type
                    in ("identifier", *syntax.qualified_types, *syntax.alias_qualified_types)
                    and not _is_same_node(child, alias_node)
                ),
                None,
            )
            if target is None:
                continue
            target_text = _dotted_name(source_bytes, target, syntax)
            if alias_node is not None:
                aliases.append((_node_text(source_bytes, alias_node), target_text))
            else:
                imported.append(target_text)
            continue
        if node.type in syntax.callable_types:
            name_node = _field_child(node, syntax.name_field)
            annotation = _field_child(node, syntax.return_type_field)
            if name_node is not None and annotation is not None:
                return_type = _base_type_name(source_bytes, annotation, syntax)
                if return_type is not None:
                    callable_name = _node_text(source_bytes, name_node)
                    if callable_name in return_types and return_types[callable_name] != (
                        return_type
                    ):
                        conflicting_returns.add(callable_name)
                    else:
                        return_types[callable_name] = return_type
        if node.type in syntax.binder_types:
            type_node = _field_child(node, syntax.type_field)
            name_node = next(
                (
                    found
                    for field in syntax.binder_name_fields
                    if (found := _field_child(node, field)) is not None
                ),
                None,
            )
            if name_node is None and syntax.binder_name_child_types:
                name_node = next(
                    (
                        child
                        for child in node.named_children
                        if child.type in syntax.binder_name_child_types
                    ),
                    None,
                )
            if name_node is None:
                continue
            type_name = (
                _base_type_name(source_bytes, type_node, syntax)
                if type_node is not None
                else _constructor_call_type(source_bytes, node, syntax)
            )
            scope = _binder_scope(node, syntax)
            if type_name is None or scope is None:
                continue
            bindings.append(
                TypeBinding(
                    name=_node_text(source_bytes, name_node),
                    type_name=type_name,
                    scope_start_byte=scope[0],
                    scope_end_byte=scope[1],
                )
            )
            continue
        if node.type in syntax.declarator_parent_types:
            type_node = _field_child(node, syntax.type_field)
            if type_node is None:
                continue
            type_name = _base_type_name(source_bytes, type_node, syntax)
            if type_name is None:
                continue
            for child in node.children:
                if child.type != syntax.declarator_type:
                    continue
                name_node = _field_child(child, syntax.name_field)
                scope = _binder_scope(node, syntax)
                if name_node is None or scope is None:
                    continue
                bindings.append(
                    TypeBinding(
                        name=_node_text(source_bytes, name_node),
                        type_name=type_name,
                        scope_start_byte=scope[0],
                        scope_end_byte=scope[1],
                    )
                )

    return FileScope(
        namespaces=tuple(namespaces),
        imported_namespaces=tuple(dict.fromkeys(imported)),
        aliases=tuple(dict.fromkeys(aliases)),
        bindings=tuple(bindings),
        return_types=tuple(
            (name, type_name)
            for name, type_name in return_types.items()
            if name not in conflicting_returns
        ),
        self_receivers=syntax.self_receivers,
    )


def _extract_references_from_tree(
    language: str,
    source_bytes: bytes,
    tree_sitter_language: Language,
    tree: Tree,
    symbols: Sequence[Symbol],
    file_path: str,
) -> list[RawReference]:
    try:
        query_text = load_query(language, "references")
    except FileNotFoundError:
        return []

    query = Query(tree_sitter_language, query_text)
    matches = QueryCursor(query).matches(tree.root_node)
    syntax = get_reference_syntax(language)

    # One usage per source span; the highest-priority matching capture names its kind.
    anchors: dict[tuple[int, int], tuple[Node, str | None]] = {}
    for _, captures in matches:
        for capture_name, nodes in captures.items():
            if capture_name != _REFERENCE_CAPTURE_PREFIX and not capture_name.startswith(
                f"{_REFERENCE_CAPTURE_PREFIX}."
            ):
                continue
            usage_kind = _usage_kind_for_capture(capture_name)
            for node in nodes:
                span = (node.start_byte, node.end_byte)
                existing = anchors.get(span)
                if existing is not None and _usage_kind_rank(existing[1]) <= _usage_kind_rank(
                    usage_kind
                ):
                    continue
                anchors[span] = (node, usage_kind)

    raw_refs: list[RawReference] = []
    for span in sorted(anchors):
        node, usage_kind = anchors[span]
        # Usages outside any indexed declaration (C# top-level statements) are still
        # real usages; they are anchored to the file rather than dropped.
        from_symbol = _enclosing_symbol(symbols, node.start_byte)
        receiver_text: str | None = None
        receiver_call: str | None = None
        if syntax is not None:
            receiver_text, receiver_call = _receiver_parts(source_bytes, node, syntax)
        raw_refs.append(
            RawReference(
                name=_decode_node_text(source_bytes, node.start_byte, node.end_byte),
                start_line=node.start_point[0] + 1,
                start_byte_col=node.start_point[1] + 1,
                start_byte=node.start_byte,
                file_path=from_symbol.file_path if from_symbol is not None else file_path,
                language=language,
                from_symbol=from_symbol,
                usage_kind=usage_kind,
                qualified_text=(
                    _qualified_text(source_bytes, node, syntax) if syntax is not None else None
                ),
                receiver_text=receiver_text,
                receiver_call=receiver_call,
            )
        )
    return raw_refs


def parse_source(
    path: Path,
    language: str,
    source_bytes: bytes,
    workspace_root: Path | None = None,
) -> ParsedSource:
    """Extract symbols and references from one in-memory parse of a source file."""
    tree_sitter_language = get_installed_language(to_treesitter_name(language))
    tree = Parser(tree_sitter_language).parse(source_bytes)
    symbols = _extract_symbols_from_tree(
        path,
        language,
        source_bytes,
        tree_sitter_language,
        tree,
        workspace_root,
    )
    references = _extract_references_from_tree(
        language,
        source_bytes,
        tree_sitter_language,
        tree,
        symbols,
        _stored_file_path(path, workspace_root),
    )
    return ParsedSource(
        symbols=symbols,
        references=references,
        scope=_build_file_scope(language, source_bytes, tree),
    )


def parse_file(path: Path, language: str, workspace_root: Path | None = None) -> list[Symbol]:
    """Parse a source file into normalized symbols."""
    tree_sitter_language = get_installed_language(to_treesitter_name(language))
    source_bytes = path.read_bytes()
    tree = Parser(tree_sitter_language).parse(source_bytes)
    return _extract_symbols_from_tree(
        path,
        language,
        source_bytes,
        tree_sitter_language,
        tree,
        workspace_root,
    )


def extract_references(
    path: Path,
    language: str,
    symbols: Sequence[Symbol],
    workspace_root: Path | None = None,
) -> list[RawReference]:
    """Extract raw symbol references from one source file."""
    tree_sitter_language = get_installed_language(to_treesitter_name(language))
    source_bytes = path.read_bytes()
    tree = Parser(tree_sitter_language).parse(source_bytes)
    return _extract_references_from_tree(
        language,
        source_bytes,
        tree_sitter_language,
        tree,
        symbols,
        _stored_file_path(path, workspace_root),
    )


def _candidate_symbol_ids(
    name: str,
    name_to_symbol_ids: Mapping[str, list[str]],
    separator: str = ".",
) -> list[str]:
    candidates: set[str] = set(name_to_symbol_ids.get(name, []))
    suffix = f"{separator}{name}"
    for indexed_name, symbol_ids in name_to_symbol_ids.items():
        if indexed_name.endswith(suffix):
            candidates.update(symbol_ids)
    return sorted(candidates)


def _unique_name_resolution(
    raw_ref: RawReference,
    name_to_symbol_ids: Mapping[str, list[str]],
) -> ResolvedReference:
    """Resolve by unique workspace-wide name only (no structural facts available)."""
    candidates = _candidate_symbol_ids(
        raw_ref.name,
        name_to_symbol_ids,
        name_separator(raw_ref.language),
    )
    if len(candidates) == 1:
        # A unique workspace-wide name match is still a syntactic heuristic,
        # never semantic proof of identity — hence MEDIUM, not HIGH.
        return ResolvedReference(candidates[0], Confidence.MEDIUM, ResolutionMethod.UNIQUE_NAME)
    resolution = ResolutionMethod.AMBIGUOUS if candidates else ResolutionMethod.UNRESOLVED
    return ResolvedReference(None, Confidence.LOW, resolution)


def build_reference_relations(
    raw_refs: Sequence[RawReference],
    name_to_symbol_ids: Mapping[str, list[str]],
    *,
    facts: ResolutionFacts | None = None,
    scope: FileScope | None = None,
) -> list[Relation]:
    """Resolve raw references into deterministic reference relations.

    Without `facts` this falls back to unique-name resolution, which is what callers
    holding only a name index (and every language lacking `ReferenceSyntax`) get.
    """
    relations: list[Relation] = []
    file_scope = scope if scope is not None else FileScope()
    declared_types_cache: dict[tuple[str, int], str] = {}
    for raw_ref in raw_refs:
        if facts is None:
            resolved = _unique_name_resolution(raw_ref, name_to_symbol_ids)
        else:
            declared_type_of: dict[str, str] = {}
            if raw_ref.receiver_text:
                cache_key = (raw_ref.receiver_text, raw_ref.start_byte)
                cached = declared_types_cache.get(cache_key)
                if cached is None:
                    found = file_scope.declared_type_at(
                        raw_ref.receiver_text,
                        raw_ref.start_byte,
                    )
                    if found is not None:
                        declared_types_cache[cache_key] = found
                        cached = found
                if cached is not None:
                    declared_type_of[raw_ref.receiver_text] = cached
            return_type_of: dict[str, str] = {}
            if raw_ref.receiver_call is not None:
                annotated_return = file_scope.return_type_of(raw_ref.receiver_call)
                if annotated_return is not None:
                    return_type_of[raw_ref.receiver_call] = annotated_return
            resolved = resolve_reference(
                name=raw_ref.name,
                qualified_text=raw_ref.qualified_text,
                receiver_text=raw_ref.receiver_text,
                receiver_call=raw_ref.receiver_call,
                start_byte=raw_ref.start_byte,
                enclosing_qualified_name=(
                    raw_ref.from_symbol.qualified_name if raw_ref.from_symbol else None
                ),
                facts=facts,
                namespace=file_scope.namespace_at(raw_ref.start_byte),
                imported_namespaces=file_scope.imported_namespaces,
                aliases=dict(file_scope.aliases),
                declared_type_of=declared_type_of,
                return_type_of=return_type_of,
                self_receivers=file_scope.self_receivers,
            )
        anchor = raw_ref.from_symbol.id if raw_ref.from_symbol is not None else raw_ref.file_path
        target = resolved.to_symbol_id or raw_ref.name
        relations.append(
            Relation(
                id=(f"references:{anchor}:{target}:{raw_ref.start_line}:{raw_ref.start_byte}"),
                kind=RelationKind.REFERENCES,
                from_symbol_id=(
                    raw_ref.from_symbol.id if raw_ref.from_symbol is not None else None
                ),
                to_symbol_id=resolved.to_symbol_id,
                from_file_path=raw_ref.file_path,
                to_file_path=None,
                to_name=raw_ref.name,
                source="tree-sitter",
                confidence=resolved.confidence,
                start_line=raw_ref.start_line,
                start_byte_col=raw_ref.start_byte_col,
                resolution=resolved.resolution,
                usage_kind=raw_ref.usage_kind,
                to_qualified_name=raw_ref.qualified_text,
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
