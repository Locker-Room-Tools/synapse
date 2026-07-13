((Decl
  (VarDecl
    (IDENTIFIER) @name
    (ErrorUnionExpr
      (SuffixExpr
        (BUILTINIDENTIFIER) @builtin
        (FnCallArguments)))) @definition.import)
  (#eq? @builtin "@import"))

((Decl
  (VarDecl
    (IDENTIFIER) @name
    (ErrorUnionExpr
      (SuffixExpr
        (ContainerDecl
          (ContainerDeclType) @kind)))) @definition.struct)
  (#eq? @kind "struct"))

(ContainerField
  field_member: (IDENTIFIER) @name) @definition.field

(ContainerDecl
  (Decl
    (FnProto
      function: (IDENTIFIER) @name)) @definition.method)

(source_file
  (Decl
    (FnProto
      function: (IDENTIFIER) @name)) @definition.function)