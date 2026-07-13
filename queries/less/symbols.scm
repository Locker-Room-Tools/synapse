(import_statement
  import: (string_value) @name) @definition.import

(variable_def
  name: (variable
    name: (variable_name) @name)) @definition.variable

(mixin_def
  name: (class_selector
    name: (class_name) @name)) @definition.function

(rule_set
  selectors: (selectors
    (class_selector
      name: (class_name) @name))) @definition.class