(package_statement
  (package_name) @name) @definition.package

(import_statement
  (type_name) @name) @definition.import

(class_declaration
  (identifier) @name) @definition.class

(interface_declaration
  (identifier) @name) @definition.interface

(function_declaration
  (identifier) @name) @definition.function

(variable_declaration
  (identifier) @name) @definition.field