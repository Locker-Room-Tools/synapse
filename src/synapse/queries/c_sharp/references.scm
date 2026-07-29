; Capture suffixes map to LanguageSpec.reference_usage_kinds ids (underscores become
; hyphens). Declaration syntax — namespace declarations and using directives — is never
; captured here; those are symbols, not usages.

; --- invocation and member access -------------------------------------------------

; `nameof` is a compiler operator, not a callable symbol, so it never counts as a usage.
(invocation_expression
  function: (identifier) @reference.invocation
  (#not-eq? @reference.invocation "nameof"))

(member_access_expression
  name: (identifier) @reference.member_access)

; `nameof(Server)` references the named declaration itself.
(invocation_expression
  function: (identifier) @_nameof_operator
  arguments: (argument_list
    (argument
      (identifier) @reference.nameof))
  (#eq? @_nameof_operator "nameof"))

(invocation_expression
  function: (identifier) @_nameof_operator
  arguments: (argument_list
    (argument
      (member_access_expression
        name: (identifier) @reference.nameof)))
  (#eq? @_nameof_operator "nameof"))

; --- construction and generics ----------------------------------------------------

(object_creation_expression
  type: (identifier) @reference.object_creation)

(generic_name
  (identifier) @reference.generic_type)

(type_argument_list
  (identifier) @reference.type_argument)

; --- declared type positions ------------------------------------------------------

(variable_declaration
  type: (identifier) @reference.declared_type)

(parameter
  type: (identifier) @reference.declared_type)

(property_declaration
  type: (identifier) @reference.declared_type)

(nullable_type
  type: (identifier) @reference.declared_type)

(array_type
  type: (identifier) @reference.declared_type)

(tuple_element
  type: (identifier) @reference.declared_type)

(foreach_statement
  type: (identifier) @reference.declared_type)

(method_declaration
  returns: (identifier) @reference.return_type)

; --- type literals, casts, and patterns -------------------------------------------

(typeof_expression
  type: (identifier) @reference.type_literal)

(default_expression
  type: (identifier) @reference.type_literal)

(cast_expression
  type: (identifier) @reference.cast_and_pattern)

(as_expression
  right: (identifier) @reference.cast_and_pattern)

(declaration_pattern
  type: (identifier) @reference.cast_and_pattern)

(catch_declaration
  type: (identifier) @reference.cast_and_pattern)

; --- attributes and base lists ----------------------------------------------------

(attribute
  name: (identifier) @reference.attribute)

(base_list
  (identifier) @reference.base_type)

; --- qualified and alias-qualified forms ------------------------------------------
; Only the trailing segment is captured; the qualifier chain is recovered in Python so
; the resolver can match a fully-qualified or dotted-suffix name.

(variable_declaration
  type: (qualified_name
    name: (identifier) @reference.declared_type))

(parameter
  type: (qualified_name
    name: (identifier) @reference.declared_type))

(property_declaration
  type: (qualified_name
    name: (identifier) @reference.declared_type))

(tuple_element
  type: (qualified_name
    name: (identifier) @reference.declared_type))

(foreach_statement
  type: (qualified_name
    name: (identifier) @reference.declared_type))

(method_declaration
  returns: (qualified_name
    name: (identifier) @reference.return_type))

(object_creation_expression
  type: (qualified_name
    name: (identifier) @reference.object_creation))

(type_argument_list
  (qualified_name
    name: (identifier) @reference.type_argument))

(base_list
  (qualified_name
    name: (identifier) @reference.base_type))

(attribute
  name: (qualified_name
    name: (identifier) @reference.attribute))

(typeof_expression
  type: (qualified_name
    name: (identifier) @reference.type_literal))

(default_expression
  type: (qualified_name
    name: (identifier) @reference.type_literal))

(cast_expression
  type: (qualified_name
    name: (identifier) @reference.cast_and_pattern))

(as_expression
  right: (qualified_name
    name: (identifier) @reference.cast_and_pattern))

(declaration_pattern
  type: (qualified_name
    name: (identifier) @reference.cast_and_pattern))

(catch_declaration
  type: (qualified_name
    name: (identifier) @reference.cast_and_pattern))
