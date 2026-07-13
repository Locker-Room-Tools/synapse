(import
  module: (module) @name) @definition.import

(type_definition
  (type_name
    name: (type_identifier) @name)) @definition.type

(data_constructor
  name: (constructor_name) @name) @definition.constructor

(constant
  name: (identifier) @name) @definition.constant

(function
  name: (identifier) @name) @definition.function