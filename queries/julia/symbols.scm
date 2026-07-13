(module_definition
  name: (identifier) @name) @definition.module

(function_definition
  (signature
    (call_expression
      (identifier) @name))) @definition.function

(macro_definition
  (signature
    (call_expression
      (identifier) @name))) @definition.function

(struct_definition
  (type_head
    (identifier) @name)) @definition.struct

(abstract_definition
  (type_head
    (identifier) @name)) @definition.type

(primitive_definition
  (type_head
    (identifier) @name)) @definition.type

(const_statement
  (assignment
    (identifier) @name)) @definition.constant

(block
  (assignment
    (identifier) @name) @definition.variable)

(source_file
  (assignment
    (identifier) @name) @definition.variable)

(import_statement
  (_) @name) @definition.import

(using_statement
  (_) @name) @definition.import