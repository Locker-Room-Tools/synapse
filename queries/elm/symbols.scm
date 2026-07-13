(module_declaration
  name: (upper_case_qid
    (upper_case_identifier) @name)) @definition.module

(import_clause
  moduleName: (upper_case_qid
    (upper_case_identifier) @name)) @definition.import

(type_alias_declaration
  name: (upper_case_identifier) @name) @definition.type

(type_declaration
  name: (upper_case_identifier) @name) @definition.type

(value_declaration
  functionDeclarationLeft: (function_declaration_left
    (lower_case_identifier) @name)) @definition.function