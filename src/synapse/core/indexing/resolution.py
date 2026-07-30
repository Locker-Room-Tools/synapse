"""Conservative structural resolution of references to indexed declarations.

The algorithm here is language-agnostic. Every syntax-specific fact it consults —
dotted qualifiers, member-access receivers, declared types, namespaces and imports —
arrives pre-extracted on `RawReference`/`FileScope`, driven by the language's
`ReferenceSyntax` metadata.

Two honesty rules shape the whole module:

* A reference is marked `EXACT` only when the syntax plus the indexed declarations
  prove exactly one target. There is no compiler here, so proof means "the source text
  names it and precisely one indexed declaration answers to that name".
* Everything weaker stays explicitly weaker. Scope narrowing (namespaces, imports,
  enclosing type) is reported as `SCOPED` at medium confidence, because a syntactic
  index cannot see extension methods, inherited members, or partial declarations.

Nothing is ever promoted because it is nearby, in the same file, or popular.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from synapse.core.models import Confidence, ResolutionMethod, SymbolKind

# Declarations that can own members addressable through a receiver.
_TYPE_KINDS: frozenset[str] = frozenset(
    {
        str(SymbolKind.CLASS),
        str(SymbolKind.INTERFACE),
        str(SymbolKind.STRUCT),
        str(SymbolKind.RECORD),
        str(SymbolKind.ENUM),
        str(SymbolKind.TYPE),
    }
)

# Structural boilerplate: never a resolution target for a usage.
_NON_TARGET_KINDS: frozenset[str] = frozenset({str(SymbolKind.NAMESPACE), str(SymbolKind.IMPORT)})


@dataclass(frozen=True, slots=True)
class ResolutionFacts:
    """Indexed declaration facts consulted while resolving references.

    `suffix_index` maps every dotted suffix of every qualified name to the declaring
    symbol ids, so both simple-name and dotted-suffix lookups are constant time
    instead of a workspace-wide scan per reference.
    """

    kinds: Mapping[str, str]
    qualified_names: Mapping[str, str]
    exact_qualified: Mapping[str, tuple[str, ...]]
    suffix_index: Mapping[str, tuple[str, ...]]
    separator: str

    def targets_for(self, dotted_name: str) -> tuple[str, ...]:
        """Return candidate ids whose qualified name ends at this dotted name."""
        return self.suffix_index.get(dotted_name, ())

    def exact_targets_for(self, qualified_name: str) -> tuple[str, ...]:
        """Return candidate ids whose qualified name is exactly this name."""
        return self.exact_qualified.get(qualified_name, ())


@dataclass(frozen=True, slots=True)
class ResolvedReference:
    """The outcome of resolving one reference site."""

    to_symbol_id: str | None
    confidence: Confidence
    resolution: ResolutionMethod


def build_resolution_facts(
    *,
    kinds: Mapping[str, str],
    qualified_names: Mapping[str, str],
    separator: str = ".",
) -> ResolutionFacts:
    """Precompute the exact and dotted-suffix lookups the resolver needs."""
    exact: dict[str, list[str]] = {}
    suffixes: dict[str, list[str]] = {}
    for symbol_id, qualified_name in qualified_names.items():
        if kinds.get(symbol_id) in _NON_TARGET_KINDS:
            continue
        exact.setdefault(qualified_name, []).append(symbol_id)
        segments = qualified_name.split(separator)
        for start in range(len(segments)):
            suffix = separator.join(segments[start:])
            bucket = suffixes.setdefault(suffix, [])
            if symbol_id not in bucket:
                bucket.append(symbol_id)
    return ResolutionFacts(
        kinds=kinds,
        qualified_names=qualified_names,
        exact_qualified={name: tuple(sorted(ids)) for name, ids in exact.items()},
        suffix_index={name: tuple(sorted(ids)) for name, ids in suffixes.items()},
        separator=separator,
    )


def _unique(candidates: Sequence[str]) -> str | None:
    return candidates[0] if len(candidates) == 1 else None


def _exact(symbol_id: str) -> ResolvedReference:
    return ResolvedReference(symbol_id, Confidence.HIGH, ResolutionMethod.EXACT)


def _scoped(symbol_id: str) -> ResolvedReference:
    return ResolvedReference(symbol_id, Confidence.MEDIUM, ResolutionMethod.SCOPED)


def _resolve_type_name(type_name: str, facts: ResolutionFacts) -> str | None:
    """Return the qualified name of the single type declaration called `type_name`."""
    candidates = [
        symbol_id
        for symbol_id in facts.targets_for(type_name)
        if facts.kinds.get(symbol_id) in _TYPE_KINDS
    ]
    unique_id = _unique(candidates)
    return facts.qualified_names.get(unique_id) if unique_id is not None else None


def _resolve_member_on_type(
    type_qualified_name: str,
    member: str,
    facts: ResolutionFacts,
) -> str | None:
    """Return the single member of a known type with this name, if there is one."""
    return _unique(facts.exact_targets_for(f"{type_qualified_name}{facts.separator}{member}"))


def _enclosing_type_qualified_name(
    enclosing_qualified_name: str | None,
    facts: ResolutionFacts,
) -> str | None:
    """Return the innermost enclosing declaration that is a type (for self receivers)."""
    if not enclosing_qualified_name:
        return None
    segments = enclosing_qualified_name.split(facts.separator)
    for end in reversed(range(1, len(segments) + 1)):
        prefix = facts.separator.join(segments[:end])
        for symbol_id in facts.exact_targets_for(prefix):
            if facts.kinds.get(symbol_id) in _TYPE_KINDS:
                return prefix
    return None


def _namespace_prefixes(namespace: str | None, separator: str) -> list[str]:
    """Return a namespace and every ancestor namespace, innermost first."""
    if not namespace:
        return []
    segments = namespace.split(separator)
    return [separator.join(segments[: index + 1]) for index in reversed(range(len(segments)))]


def resolve_reference(
    *,
    name: str,
    qualified_text: str | None,
    receiver_text: str | None,
    start_byte: int,
    enclosing_qualified_name: str | None,
    facts: ResolutionFacts,
    namespace: str | None = None,
    imported_namespaces: Sequence[str] = (),
    aliases: Mapping[str, str] | None = None,
    declared_type_of: Mapping[str, str] | None = None,
    receiver_call: str | None = None,
    return_type_of: Mapping[str, str] | None = None,
    self_receivers: Sequence[str] = (),
) -> ResolvedReference:
    """Bind one reference to a declaration, or report it ambiguous/unresolved.

    Proof-carrying strategies run before scope-narrowing ones, so a reference is never
    downgraded to `SCOPED` when the syntax already determined it.
    """
    separator = facts.separator
    alias_map = aliases or {}
    declared_types = declared_type_of or {}

    # 1. The source names a fully-qualified declaration outright.
    if qualified_text:
        resolved_alias = alias_map.get(qualified_text.split(separator)[0])
        dotted = qualified_text
        if resolved_alias is not None:
            remainder = qualified_text.split(separator)[1:]
            dotted = separator.join([resolved_alias, *remainder])
        exact_id = _unique(facts.exact_targets_for(dotted))
        if exact_id is not None:
            return _exact(exact_id)
        # 2. The dotted name is an unambiguous suffix of one qualified name.
        suffix_id = _unique(facts.targets_for(dotted))
        if suffix_id is not None:
            return _exact(suffix_id)

    # 4. The receiver's type is syntactically known, so the member is determined.
    if receiver_text or receiver_call:
        type_qualified_name: str | None = None
        if receiver_text is not None and receiver_text in self_receivers:
            # `self`/`cls` receivers denote the innermost enclosing type.
            type_qualified_name = _enclosing_type_qualified_name(enclosing_qualified_name, facts)
        elif receiver_call is not None:
            # A factory-call receiver is typed by an explicit return annotation; a
            # call that names a type directly is a constructor and types itself.
            annotated_return = (return_type_of or {}).get(receiver_call)
            if annotated_return is not None:
                type_qualified_name = _resolve_type_name(annotated_return, facts)
            if type_qualified_name is None:
                type_qualified_name = _resolve_type_name(receiver_call.split(separator)[-1], facts)
        elif receiver_text is not None:
            receiver_type = declared_types.get(receiver_text)
            if receiver_type is not None:
                type_qualified_name = _resolve_type_name(receiver_type, facts)
            if type_qualified_name is None:
                # A receiver that *is* a type name (static access) resolves the same way.
                type_qualified_name = _resolve_type_name(receiver_text.split(separator)[-1], facts)
        if type_qualified_name is not None:
            member_id = _resolve_member_on_type(type_qualified_name, name, facts)
            if member_id is not None:
                return _exact(member_id)

    candidates = list(facts.targets_for(name))

    # 3. Namespace and import scope narrow same-name candidates to one.
    if len(candidates) > 1:
        in_scope_prefixes = [
            *_namespace_prefixes(namespace, separator),
            *imported_namespaces,
            *alias_map.values(),
        ]
        if in_scope_prefixes:
            narrowed = [
                symbol_id
                for symbol_id in candidates
                if any(
                    facts.qualified_names.get(symbol_id, "") == f"{prefix}{separator}{name}"
                    for prefix in in_scope_prefixes
                )
            ]
            scoped_id = _unique(narrowed)
            if scoped_id is not None:
                return _scoped(scoped_id)

    # 5. The enclosing type declares exactly one member with this name.
    if len(candidates) > 1 and enclosing_qualified_name:
        segments = enclosing_qualified_name.split(separator)
        for depth in reversed(range(1, len(segments) + 1)):
            container = separator.join(segments[:depth])
            member_id = _unique(facts.exact_targets_for(f"{container}{separator}{name}"))
            if member_id is not None and member_id in candidates:
                return _scoped(member_id)

    # 6. A unique workspace-wide name match is still only a syntactic heuristic.
    unique_id = _unique(candidates)
    if unique_id is not None:
        return ResolvedReference(unique_id, Confidence.MEDIUM, ResolutionMethod.UNIQUE_NAME)

    # 7. Nothing proven: stay honestly ambiguous or unresolved.
    resolution = ResolutionMethod.AMBIGUOUS if candidates else ResolutionMethod.UNRESOLVED
    return ResolvedReference(None, Confidence.LOW, resolution)
