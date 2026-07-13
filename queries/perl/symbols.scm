(package_statement
  name: (package) @name) @definition.package

(use_statement
  module: (package) @name) @definition.import

(assignment_expression
  left: (variable_declaration
    variable: (scalar
      (varname) @name))) @definition.variable

(subroutine_declaration_statement
  name: (bareword) @name) @definition.function