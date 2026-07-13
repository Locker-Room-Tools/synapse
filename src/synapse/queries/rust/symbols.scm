(use_declaration
  argument: (_) @name) @definition.import

(mod_item
  name: (identifier) @name) @definition.module

(struct_item
  name: (type_identifier) @name) @definition.struct

(enum_item
  name: (type_identifier) @name) @definition.enum

(union_item
  name: (type_identifier) @name) @definition.struct

(trait_item
  name: (type_identifier) @name) @definition.interface

(type_item
  name: (type_identifier) @name) @definition.type

(const_item
  name: (identifier) @name) @definition.constant

(static_item
  name: (identifier) @name) @definition.constant

(impl_item
  body: (declaration_list
    (function_item
      name: (identifier) @name) @definition.method))

(source_file
  (function_item
    name: (identifier) @name) @definition.function)

(mod_item
  body: (declaration_list
    (function_item
      name: (identifier) @name) @definition.function))

(field_declaration
  name: (field_identifier) @name) @definition.field