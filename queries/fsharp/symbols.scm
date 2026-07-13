(named_module
  name: (long_identifier) @name) @definition.module

(import_decl
  (long_identifier) @name) @definition.import

(type_definition
  (record_type_defn
    (type_name
      type_name: (identifier) @name))) @definition.record

(declaration_expression
  (function_or_value_defn
    (function_declaration_left
      (identifier) @name))) @definition.function

(declaration_expression
  (function_or_value_defn
    (value_declaration_left
      (identifier_pattern
        (long_identifier_or_op
          (identifier) @name))))) @definition.variable