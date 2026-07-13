(use_statement
  (string_value) @name) @definition.import

(import_statement
  (string_value) @name) @definition.import

(stylesheet
  (declaration
    (property_name) @name) @definition.variable)

(mixin_statement
  name: (identifier) @name) @definition.function

(function_statement
  name: (identifier) @name) @definition.function

(rule_set
  (selectors
    (class_selector
      (class_name) @name))) @definition.class

(rule_set
  (selectors
    (placeholder
      (identifier) @name))) @definition.class