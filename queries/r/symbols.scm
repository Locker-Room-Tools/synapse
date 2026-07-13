(binary_operator
  lhs: (identifier) @name
  operator: ["<-" "=" "<<-"]
  rhs: (function_definition)) @definition.function

(binary_operator
  lhs: (identifier) @name
  operator: ["<-" "=" "<<-"]
  rhs: [(call) (identifier) (float) (string)]) @definition.variable

(binary_operator
  lhs: [(call) (identifier) (float) (string)]
  operator: ["->" "->>"]
  rhs: (identifier) @name) @definition.variable

(binary_operator
  lhs: (parenthesized_expression (function_definition))
  operator: ["->" "->>"]
  rhs: (identifier) @name) @definition.function

((call
  function: (identifier) @_import_fn
  arguments: (arguments
    (argument (identifier) @name))) @definition.import
  (#any-of? @_import_fn "library" "require"))