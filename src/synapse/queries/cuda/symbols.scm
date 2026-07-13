(preproc_include
  path: (_) @name) @definition.import

(preproc_def
  name: (identifier) @name) @definition.constant

(type_definition
  declarator: (type_identifier) @name) @definition.type

(struct_specifier
  name: (type_identifier) @name) @definition.struct

(union_specifier
  name: (type_identifier) @name) @definition.struct

(enum_specifier
  name: (type_identifier) @name) @definition.enum

(function_definition
  declarator: (function_declarator
    declarator: (identifier) @name)) @definition.function

(field_declaration
  declarator: (field_identifier) @name) @definition.field

(translation_unit
  (declaration
    declarator: (init_declarator
      declarator: (identifier) @name)) @definition.variable)