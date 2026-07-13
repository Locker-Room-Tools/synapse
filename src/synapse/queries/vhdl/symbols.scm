(use_clause
  (selected_name) @name) @definition.import

(package_declaration
  name: (identifier) @name) @definition.module

(entity_declaration
  name: (identifier) @name) @definition.module

(architecture_body
  name: (identifier) @name) @definition.module

(function_declaration
  designator: (identifier) @name) @definition.function

(constant_declaration
  (identifier_list
    (identifier) @name)) @definition.constant

(signal_declaration
  (identifier_list
    (identifier) @name)) @definition.variable