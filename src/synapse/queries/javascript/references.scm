; Capture suffixes map to LanguageSpec.reference_usage_kinds ids (underscores become
; hyphens). Both advertised kinds prove a call: a direct call, a member call, and a
; `new` expression all transfer control into the target. Receiver types, dynamic
; dispatch, and non-call references are not extracted and stay advertised as
; limitations.

(call_expression
  function: (identifier) @reference.invocation)

(call_expression
  function: (member_expression
    property: (property_identifier) @reference.invocation))

(new_expression
  constructor: (identifier) @reference.object_creation)