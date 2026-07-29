"""Enumerations shared by the Synapse symbol model."""

from enum import StrEnum


class SymbolKind(StrEnum):
    """Normalized, language-agnostic symbol categories."""

    NAMESPACE = "namespace"
    PACKAGE = "package"
    MODULE = "module"
    CLASS = "class"
    INTERFACE = "interface"
    STRUCT = "struct"
    RECORD = "record"
    ENUM = "enum"
    TYPE = "type"
    FUNCTION = "function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    PROPERTY = "property"
    FIELD = "field"
    VARIABLE = "variable"
    CONSTANT = "constant"
    IMPORT = "import"


class RelationKind(StrEnum):
    """Supported relationships between symbols."""

    CONTAINS = "contains"
    REFERENCES = "references"
    IMPORTS = "imports"
    MENTIONS = "mentions"


class Confidence(StrEnum):
    """How trustworthy an extracted symbol or relation is."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ResolutionMethod(StrEnum):
    """How a reference relation was bound to its target symbol."""

    # The source syntax plus the indexed declarations prove exactly one target:
    # a fully-qualified name, an unambiguous dotted suffix, or a member reached
    # through a receiver whose type is declared in the source.
    EXACT = "exact"
    # Narrowed to one candidate by namespace, import, or enclosing-type scope.
    # Stronger than a bare name match, but a syntactic index cannot see extension
    # methods, inherited members, or partial declarations — hence not EXACT.
    SCOPED = "scoped"
    UNIQUE_NAME = "unique-name"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
