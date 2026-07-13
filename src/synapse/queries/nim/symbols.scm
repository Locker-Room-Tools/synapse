(importStmt
  (expr
    (primary
      (symbol) @name))) @definition.import

(constant
  (declColonEquals
    (symbol) @name)) @definition.constant

(variable
  (declColonEquals
    (symbol) @name)) @definition.variable

(typeDef
  (symbol) @name) @definition.type

(routine
  (symbol) @name) @definition.function