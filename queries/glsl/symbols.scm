(struct_specifier
  name: (type_identifier) @name) @definition.struct

(function_definition
  declarator: (function_declarator
    declarator: (identifier) @name)) @definition.function

(field_declaration
  declarator: (field_identifier) @name) @definition.field

(translation_unit
  (declaration
    declarator: (identifier) @name) @definition.variable)

(translation_unit
  (declaration
    declarator: (init_declarator
      declarator: (identifier) @name)) @definition.variable)