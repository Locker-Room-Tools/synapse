(import_statement
  source: (string) @name) @definition.import

(class_declaration
  name: (type_identifier) @name) @definition.class

(abstract_class_declaration
  name: (type_identifier) @name) @definition.class

(interface_declaration
  name: (type_identifier) @name) @definition.interface

(enum_declaration
  name: (identifier) @name) @definition.enum

(type_alias_declaration
  name: (type_identifier) @name) @definition.type

(function_declaration
  name: (identifier) @name) @definition.function

(method_definition
  name: (property_identifier) @name) @definition.method

(public_field_definition
  name: (property_identifier) @name) @definition.field

(program
  (lexical_declaration
    (variable_declarator
      name: (identifier) @name)) @definition.variable)

(export_statement
  (lexical_declaration
    (variable_declarator
      name: (identifier) @name)) @definition.variable)