(purescript
  name: (qualified_module
    (module) @name)) @definition.module

(import
  module: (qualified_module
    (module) @name)) @definition.import

(data
  name: (type) @name) @definition.type

(newtype
  name: (type) @name) @definition.type

(type_alias
  name: (type) @name) @definition.type

(class_declaration
  (class_head
    (class_name
      (type) @name))) @definition.type

(class_instance
  (instance_name) @name) @definition.type

(foreign_import
  name: (variable) @name) @definition.function

(function
  name: (variable) @name) @definition.function