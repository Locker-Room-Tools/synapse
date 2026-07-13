(document
  (element
    (start_tag
      (tag_name) @name)) @definition.module)

((attribute
  (attribute_name) @name) @definition.variable
  (#match? @name "^#"))