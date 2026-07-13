(module_definition
  (module_binding
    (module_name) @name)) @definition.module

(type_definition
  (type_binding
    name: (type_constructor) @name)) @definition.type

(value_definition
  (let_binding
    pattern: (value_name) @name
    (parameter))) @definition.function

(value_definition
  (let_binding
    pattern: (value_name) @name)) @definition.variable