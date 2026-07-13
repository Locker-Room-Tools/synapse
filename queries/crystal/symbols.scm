(require
  (string) @name) @definition.import

(module_def
  name: (constant) @name) @definition.module

(class_def
  name: (constant) @name) @definition.class

(struct_def
  name: (constant) @name) @definition.struct

(class_def
  (method_def
    name: (identifier) @name) @definition.method)

(source_file
  (method_def
    name: (identifier) @name) @definition.function)

(const_assign
  lhs: (constant) @name) @definition.constant

(type_declaration
  var: (instance_var) @name) @definition.field