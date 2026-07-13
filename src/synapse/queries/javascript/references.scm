(call_expression
  function: (identifier) @reference)

(call_expression
  function: (member_expression
    property: (property_identifier) @reference))

(new_expression
  constructor: (identifier) @reference)