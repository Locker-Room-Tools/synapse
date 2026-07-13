((list_lit
  (sym_lit) @keyword
  (kwd_lit) @name) @definition.module
  (#eq? @keyword "defpackage"))

(defun
  (defun_header
    function_name: (sym_lit) @name)) @definition.function

((list_lit
  (sym_lit) @keyword
  (sym_lit) @name) @definition.function
  (#eq? @keyword "defmacro"))

((list_lit
  (sym_lit) @keyword
  (sym_lit) @name) @definition.class
  (#eq? @keyword "defclass"))

((list_lit
  (sym_lit) @keyword
  (sym_lit) @name) @definition.struct
  (#eq? @keyword "defstruct"))

((list_lit
  (sym_lit) @keyword
  (sym_lit) @name) @definition.function
  (#eq? @keyword "defgeneric"))

((list_lit
  (sym_lit) @keyword
  (sym_lit) @name) @definition.method
  (#eq? @keyword "defmethod"))

((list_lit
  (sym_lit) @keyword
  (sym_lit) @name) @definition.variable
  (#eq? @keyword "defvar"))

((list_lit
  (sym_lit) @keyword
  (sym_lit) @name) @definition.variable
  (#eq? @keyword "defparameter"))

((list_lit
  (sym_lit) @keyword
  (sym_lit) @name) @definition.constant
  (#eq? @keyword "defconstant"))