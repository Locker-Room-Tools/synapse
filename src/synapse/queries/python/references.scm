; Capture suffixes map to LanguageSpec.reference_usage_kinds ids (underscores become
; hyphens). Only `invocation` is advertised as call-proven: a base list and a bare
; decorator name are declaration positions, reported neutrally with their usage kind.

(call
  function: (identifier) @reference.invocation)

(call
  function: (attribute
    attribute: (identifier) @reference.invocation))

(class_definition
  superclasses: (argument_list
    (identifier) @reference.base_type))

(decorator
  (identifier) @reference.decorator)
