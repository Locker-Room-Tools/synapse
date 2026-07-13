(decl_def
  (cmd_identifier) @name) @definition.function

(decl_module
  (cmd_identifier) @name) @definition.module

(stmt_let
  (identifier) @name) @definition.variable

(stmt_const
  (identifier) @name) @definition.constant