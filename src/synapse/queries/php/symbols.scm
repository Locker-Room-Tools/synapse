(namespace_definition
  name: (namespace_name) @name) @definition.namespace
(namespace_use_clause
  (name) @name) @definition.import
(const_declaration
  (const_element (name) @name)) @definition.constant
(interface_declaration
  name: (name) @name) @definition.interface
(trait_declaration
  name: (name) @name) @definition.class
(class_declaration
  name: (name) @name) @definition.class
(enum_declaration
  name: (name) @name) @definition.enum
(property_declaration
  (property_element (variable_name (name) @name))) @definition.field
(method_declaration
  name: (name) @name) @definition.method
(function_definition
  name: (name) @name) @definition.function
