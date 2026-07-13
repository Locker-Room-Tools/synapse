((command
  (unit
    (identifier) @keyword)
  (unit
    (identifier) @name)) @definition.package
  (#eq? @keyword "package"))

((command
  (unit
    (identifier) @keyword)
  (unit) @name) @definition.import
  (#eq? @keyword "import"))

((command
  (unit
    (identifier) @keyword)
  (block
    (unit
      (identifier) @name))) @definition.class
  (#eq? @keyword "class"))

(block
  (command
    (unit)
    (unit
      (identifier) @name)) @definition.field)

(block
  (command
    (unit)
    (block
      (unit
        (func
          (identifier) @name)))) @definition.method)

((command
  (unit
    (identifier) @keyword)
  (block
    (unit
      (func
        (identifier) @name)))) @definition.function
  (#eq? @keyword "def"))