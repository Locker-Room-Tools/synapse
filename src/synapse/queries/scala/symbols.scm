(package_clause
  (package_identifier) @name) @definition.package
(import_declaration) @definition.import
(object_definition
  name: (identifier) @name) @definition.module
(class_definition
  name: (identifier) @name) @definition.class
(trait_definition
  name: (identifier) @name) @definition.interface
(function_definition
  name: (identifier) @name) @definition.function
(function_declaration
  name: (identifier) @name) @definition.function
(val_definition
  pattern: (identifier) @name) @definition.field
