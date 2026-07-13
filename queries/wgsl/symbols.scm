(struct_declaration
  name: (identifier) @name) @definition.struct

(struct_member
  (variable_identifier_declaration
    name: (identifier) @name)) @definition.field

(global_constant_declaration
  (variable_identifier_declaration
    name: (identifier) @name)) @definition.constant

(global_variable_declaration
  (variable_declaration
    (variable_identifier_declaration
      name: (identifier) @name))) @definition.variable

(function_declaration
  name: (identifier) @name) @definition.function