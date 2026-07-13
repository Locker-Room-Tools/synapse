((list_lit
  value: (sym_lit name: (sym_name) @keyword)
  value: (sym_lit name: (sym_name) @name)) @definition.module
  (#eq? @keyword "ns"))

((list_lit
  value: (sym_lit name: (sym_name) @ns_keyword)
  value: (sym_lit name: (sym_name))
  value: (list_lit
    value: (kwd_lit name: (kwd_name) @require_keyword)
    value: (vec_lit
      value: (sym_lit name: (sym_name) @name)))) @definition.import
  (#eq? @ns_keyword "ns")
  (#eq? @require_keyword ":require"))

((list_lit
  value: (sym_lit name: (sym_name) @keyword)
  value: (sym_lit name: (sym_name) @name)) @definition.variable
  (#eq? @keyword "def"))

((list_lit
  value: (sym_lit name: (sym_name) @keyword)
  value: (sym_lit name: (sym_name) @name)
  value: (vec_lit)) @definition.function
  (#eq? @keyword "defn"))

((list_lit
  value: (sym_lit name: (sym_name) @keyword)
  value: (sym_lit name: (sym_name) @name)
  value: (vec_lit)) @definition.record
  (#eq? @keyword "defrecord"))

((list_lit
  value: (sym_lit name: (sym_name) @keyword)
  value: (sym_lit name: (sym_name) @name)) @definition.interface
  (#eq? @keyword "defprotocol"))

((list_lit
  value: (sym_lit name: (sym_name) @keyword)
  value: (sym_lit name: (sym_name) @name)
  value: (vec_lit)) @definition.type
  (#eq? @keyword "deftype"))