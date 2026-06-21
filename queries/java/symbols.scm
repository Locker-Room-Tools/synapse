(package_declaration
  (_) @name) @definition.package

(import_declaration
  (scoped_identifier) @name) @definition.import

(class_declaration
  name: (identifier) @name) @definition.class

(interface_declaration
  name: (identifier) @name) @definition.interface

(enum_declaration
  name: (identifier) @name) @definition.enum

(record_declaration
  name: (identifier) @name) @definition.record

(constructor_declaration
  name: (identifier) @name) @definition.constructor

(method_declaration
  name: (identifier) @name) @definition.method

(field_declaration
  declarator: (variable_declarator
    name: (identifier) @name)) @definition.field

(enum_constant
  name: (identifier) @name) @definition.constant