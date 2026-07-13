(function_statement
  (function_name) @name) @definition.function

(class_statement
  (simple_name) @name) @definition.class

(class_method_definition
  (simple_name) @name) @definition.method

(class_property_definition
  (variable) @name) @definition.field