(create_table
  (object_reference
    name: (identifier) @name)) @definition.struct

(create_table
  (column_definitions
    (column_definition
      name: (identifier) @name) @definition.field))

(create_view
  (object_reference
    name: (identifier) @name)) @definition.struct