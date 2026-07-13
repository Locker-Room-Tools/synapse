(function_definition
  (word) @name) @definition.function

((command
  (word) @cmd
  (word) @name) @definition.variable
  (#eq? @cmd "set"))