(import_declaration
  (identifier) @name) @definition.import

(class_declaration
  declaration_kind: "class"
  name: (type_identifier) @name) @definition.class

(class_declaration
  declaration_kind: "struct"
  name: (type_identifier) @name) @definition.struct

(class_declaration
  declaration_kind: "actor"
  name: (type_identifier) @name) @definition.class

(class_declaration
  declaration_kind: "enum"
  name: (type_identifier) @name) @definition.enum

(protocol_declaration
  name: (type_identifier) @name) @definition.interface

(enum_entry
  name: (simple_identifier) @name) @definition.constant

(class_body
  (function_declaration
    name: (simple_identifier) @name) @definition.method)

(source_file
  (function_declaration
    name: (simple_identifier) @name) @definition.function)

(property_declaration
  name: (pattern
    (simple_identifier) @name)) @definition.field