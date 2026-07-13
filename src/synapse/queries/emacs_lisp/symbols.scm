(function_definition
  (symbol) @name) @definition.function

(macro_definition
  (symbol) @name) @definition.function

((special_form
  (symbol) @keyword
  (symbol) @name) @definition.variable
  (#eq? @keyword "defvar"))

((special_form
  (symbol) @keyword
  (symbol) @name) @definition.constant
  (#eq? @keyword "defconst"))