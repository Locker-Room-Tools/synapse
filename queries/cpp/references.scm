(call_expression
  function: (identifier) @reference)

(call_expression
  function: (field_expression
    field: (field_identifier) @reference))

(call_expression
  function: (qualified_identifier
    name: (identifier) @reference))

(new_expression
  type: (type_identifier) @reference)