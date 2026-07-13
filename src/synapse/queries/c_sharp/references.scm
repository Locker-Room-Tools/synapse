(invocation_expression
  function: (identifier) @reference)

(invocation_expression
  function: (member_access_expression
    name: (identifier) @reference))

(object_creation_expression
  type: (identifier) @reference)

(base_list
  (_
    (identifier) @reference))