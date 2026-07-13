((call
  target: (identifier) @keyword
  (arguments
    (alias) @name)) @definition.module
  (#eq? @keyword "defmodule"))

((call
  target: (identifier) @keyword
  (arguments
    (alias) @name)) @definition.import
  (#eq? @keyword "import"))

((call
  target: (identifier) @keyword
  (arguments
    (call
      target: (identifier) @name))) @definition.function
  (#eq? @keyword "def"))

((call
  target: (identifier) @keyword
  (arguments
    (call
      target: (identifier) @name))) @definition.function
  (#eq? @keyword "defp"))

((call
  target: (identifier) @keyword
  (arguments
    (list
      (atom) @name))) @definition.field
  (#eq? @keyword "defstruct"))