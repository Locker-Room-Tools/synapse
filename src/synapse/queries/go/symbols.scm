(package_clause
  (package_identifier) @name) @definition.package

(import_spec
  path: (interpreted_string_literal) @name) @definition.import

(function_declaration
  name: (identifier) @name) @definition.function

(method_declaration
  name: (field_identifier) @name) @definition.method

(type_declaration
  (type_spec
    name: (type_identifier) @name
    type: (struct_type))) @definition.struct

(type_declaration
  (type_spec
    name: (type_identifier) @name
    type: (interface_type))) @definition.interface

(field_declaration
  name: (field_identifier) @name) @definition.field

(source_file
  (const_declaration
    (const_spec
      name: (identifier) @name)) @definition.constant)

(source_file
  (var_declaration
    (var_spec
      name: (identifier) @name)) @definition.variable)