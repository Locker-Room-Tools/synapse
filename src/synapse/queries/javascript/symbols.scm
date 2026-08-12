(import_statement
  source: (string) @name) @definition.import

(class_declaration
  name: (identifier) @name) @definition.class

(function_declaration
  name: (identifier) @name) @definition.function

(generator_function_declaration
  name: (identifier) @name) @definition.function

(method_definition
  name: (property_identifier) @name) @definition.method

(field_definition
  property: (property_identifier) @name) @definition.field

(program
  (lexical_declaration
    (variable_declarator
      name: (identifier) @name)) @definition.variable)

(export_statement
  (lexical_declaration
    (variable_declarator
      name: (identifier) @name)) @definition.variable)

(program
  (variable_declaration
    (variable_declarator
      name: (identifier) @name)) @definition.variable)

(export_statement
  (variable_declaration
    (variable_declarator
      name: (identifier) @name)) @definition.variable)