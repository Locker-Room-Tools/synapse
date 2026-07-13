(program
  (moduleName
    (identifier) @name)) @definition.module

(declUses
  (moduleName
    (identifier) @name)) @definition.import

(declType
  name: (identifier) @name) @definition.type

(declVar
  name: (identifier) @name) @definition.variable

(defProc
  header: (declProc
    name: (identifier) @name)) @definition.function