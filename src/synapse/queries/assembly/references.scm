((instruction
  kind: (word) @kind
  (ident) @reference)
  (#match? @kind "^(call|jmp|je|jne|jg|jge|jl|jle|bl|b)$"))