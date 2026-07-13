(with_clause
  (_) @name) @definition.import

(use_clause
  (_) @name) @definition.import

(package_declaration
  name: (identifier) @name) @definition.module

(package_body
  name: (identifier) @name) @definition.module

(subprogram_declaration
  (procedure_specification
    name: (identifier) @name)) @definition.function

(subprogram_declaration
  (function_specification
    name: (identifier) @name)) @definition.function

(subprogram_body
  (procedure_specification
    name: (identifier) @name)) @definition.function

(subprogram_body
  (function_specification
    name: (identifier) @name)) @definition.function

(full_type_declaration
  (identifier) @name) @definition.type

(object_declaration
  name: (identifier) @name) @definition.variable