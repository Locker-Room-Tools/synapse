(namespace_declaration
  name: (_) @name) @definition.namespace

(file_scoped_namespace_declaration
  name: (_) @name) @definition.namespace

(class_declaration
  name: (identifier) @name) @definition.class

(struct_declaration
  name: (identifier) @name) @definition.struct

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

(local_function_statement
  name: (identifier) @name) @definition.function

(delegate_declaration
  name: (identifier) @name) @definition.type

(event_field_declaration
  (variable_declaration
    (variable_declarator
      name: (identifier) @name))) @definition.field

(enum_member_declaration
  name: (identifier) @name) @definition.constant

(property_declaration
  name: (identifier) @name) @definition.property

(field_declaration
  (variable_declaration
    (variable_declarator
      name: (identifier) @name))) @definition.field

(using_directive
  (identifier) @name) @definition.import

(using_directive
  (qualified_name) @name) @definition.import