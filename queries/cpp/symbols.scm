(preproc_include
  path: (_) @name) @definition.import

(namespace_definition
  name: (namespace_identifier) @name) @definition.namespace

(class_specifier
  name: (type_identifier) @name) @definition.class

(struct_specifier
  name: (type_identifier) @name) @definition.struct

(union_specifier
  name: (type_identifier) @name) @definition.struct

(enum_specifier
  name: (type_identifier) @name) @definition.enum

(type_definition
  declarator: (type_identifier) @name) @definition.type

(alias_declaration
  name: (type_identifier) @name) @definition.type

(function_definition
  declarator: (function_declarator
    declarator: (field_identifier) @name)) @definition.method

(translation_unit
  (function_definition
    declarator: (function_declarator
      declarator: (identifier) @name)) @definition.function)

(namespace_definition
  (declaration_list
    (function_definition
      declarator: (function_declarator
        declarator: (identifier) @name)) @definition.function))

(field_declaration
  declarator: (field_identifier) @name) @definition.field