(package_declaration
  (package_identifier
    (simple_identifier) @name)) @definition.module

(module_declaration
  (module_header
    (simple_identifier) @name)) @definition.module

(parameter_declaration
  (list_of_param_assignments
    (param_assignment
      (parameter_identifier) @name))) @definition.constant

(data_declaration
  (list_of_variable_decl_assignments
    (_) @name)) @definition.variable

(function_declaration
  (function_body_declaration
    (function_identifier) @name)) @definition.function

(task_declaration
  (task_body_declaration
    (task_identifier) @name)) @definition.function