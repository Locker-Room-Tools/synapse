(program
  (program_statement
    (name) @name)) @definition.module

(module
  (module_statement
    (name) @name)) @definition.module

(use_statement
  (module_name) @name) @definition.import

(subroutine
  (subroutine_statement
    name: (name) @name)) @definition.function

(function
  (function_statement
    name: (name) @name)) @definition.function

(derived_type_definition
  (derived_type_statement
    (type_name) @name)) @definition.type

(variable_declaration
  declarator: (identifier) @name) @definition.variable