(class_definition
  name: (identifier) @name) @definition.class
(mixin_declaration
  (identifier) @name) @definition.class
(enum_declaration
  name: (identifier) @name) @definition.enum
(function_signature
  name: (identifier) @name) @definition.function
(method_signature
  (function_signature
    name: (identifier) @name)) @definition.method
