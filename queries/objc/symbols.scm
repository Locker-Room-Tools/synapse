(preproc_include
  path: (system_lib_string) @name) @definition.import

(class_interface
  (identifier) @name) @definition.class

(property_declaration
  (struct_declaration
    (struct_declarator
      (pointer_declarator
        declarator: (identifier) @name)))) @definition.property

(property_declaration
  (struct_declaration
    (struct_declarator
      (identifier) @name))) @definition.property

(method_definition
  (identifier) @name) @definition.method