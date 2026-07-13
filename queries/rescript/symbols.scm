(open_statement
  (module_identifier) @name) @definition.import

(module_declaration
  (module_binding
    name: (module_identifier) @name)) @definition.module

(type_declaration
  (type_binding
    name: (type_identifier) @name)) @definition.type

(record_type_field
  (property_identifier) @name) @definition.property

(let_declaration
  (let_binding
    pattern: (value_identifier) @name)) @definition.function

(external_declaration
  (value_identifier) @name) @definition.function