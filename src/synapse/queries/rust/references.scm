(call_expression
  function: (identifier) @reference)

(call_expression
  function: (field_expression
    field: (field_identifier) @reference))

(call_expression
  function: (scoped_identifier
    name: (identifier) @reference))

(macro_invocation
  macro: (identifier) @reference)

(struct_expression
  name: (type_identifier) @reference)