(module_attribute
  (atom) @name) @definition.module

(import_attribute
  module: (atom) @name) @definition.import

(record_decl
  name: (atom) @name) @definition.record

(record_field
  name: (atom) @name) @definition.field

(fun_decl
  clause: (function_clause
    name: (atom) @name)) @definition.function