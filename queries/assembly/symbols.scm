(program
  (label
    (ident) @name)) @definition.module

(label
  (ident) @name) @definition.function

((meta
  kind: (meta_ident) @kind
  (ident) @name) @definition.constant
  (#match? @kind "^\\.(equ|set)$"))