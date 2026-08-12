(class_definition
  name: (identifier) @name) @definition.class

(module
  (function_definition
    name: (identifier) @name) @definition.function)

(module
  (decorated_definition
    (function_definition
      name: (identifier) @name) @definition.function))

(class_definition
  body: (block
    (function_definition
      name: (identifier) @name) @definition.method))

(class_definition
  body: (block
    (decorated_definition
      (function_definition
        name: (identifier) @name) @definition.method)))

(module
  (assignment
    left: (identifier) @name) @definition.variable)

(class_definition
  body: (block
    (assignment
      left: (identifier) @name) @definition.field))

(import_statement
  name: (_) @name) @definition.import

(import_from_statement
  module_name: (_) @name) @definition.import