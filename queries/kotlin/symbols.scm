(package_header
  (identifier) @name) @definition.package

(import_header
  (identifier) @name) @definition.import

(class_declaration
  "interface"
  (type_identifier) @name) @definition.interface

(class_declaration
  (type_identifier) @name
  (enum_class_body)) @definition.enum

(class_declaration
  "class"
  (type_identifier) @name
  (class_body)) @definition.class

(object_declaration
  (type_identifier) @name) @definition.class

(enum_entry
  (simple_identifier) @name) @definition.constant

(class_body
  (function_declaration
    (simple_identifier) @name) @definition.method)

(source_file
  (function_declaration
    (simple_identifier) @name) @definition.function)

(property_declaration
  (variable_declaration
    (simple_identifier) @name)) @definition.field