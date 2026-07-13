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
