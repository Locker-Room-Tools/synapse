(module_declaration
  (module_fqn) @name) @definition.module

(import_declaration
  (imported
    (module_fqn) @name)) @definition.import

(class_declaration
  (identifier) @name) @definition.class

(interface_declaration
  (identifier) @name) @definition.interface

(struct_declaration
  (identifier) @name) @definition.struct

(enum_declaration
  (identifier) @name) @definition.enum

(aggregate_body
  (function_declaration
    (identifier) @name) @definition.method)

(source_file
  (function_declaration
    (identifier) @name) @definition.function)

(variable_declaration
  (declarator
    (identifier) @name)) @definition.variable

(auto_declaration
  variable: (identifier) @name) @definition.variable