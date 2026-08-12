# Evidence Semantics

Read this reference when coverage, an empty relation group, or a non-exact resolution
could change the answer.

## Resolution

- `exact` and `scoped` are index-local syntactic and structural evidence, not compiler
  or runtime proof.
- `unique-name` is a workspace-name heuristic.
- `ambiguous` and `unresolved` are hypotheses, not confirmed relations.

Do not use shell results to silently strengthen a heuristic or hypothesis into a proven
relation.

## Call kinds

`callers` and `callees` contain only sites whose stored usage kind proves a call for the
language. Other references, such as declared types, base types, decorators, attributes,
and member reads, remain visible in `refs_in` or `refs_out` with their usage kind.

An empty `callers` or `callees` group means that Synapse proved no indexed call under the
reported coverage. It does not prove that no call exists.

Check `coverage.extraction[].call_kinds` before interpreting an empty call relation. An
empty `call_kinds` list means that the language provides no call evidence; relevant uses
may still appear in `refs_in` or `refs_out`. When coverage reports other indexed
languages with `evidence: false`, include their stated limitations in the conclusion.

## Coverage and bounds

Both navigation tools bound their responses. Check `payload_complete`, omitted counts,
and `coverage` before treating a result as complete.

- An empty or truncated result is not proof of absence.
- `payload_complete: true` means the bounded payload was emitted completely; it does not
  mean the repository evidence is exhaustive.
- A coverage limitation matters only when the omitted or unsupported evidence could
  change a required fact.

Close a relevant coverage gap with one narrower orientation, a returned relation handle,
or a targeted shell fallback. Do not request a broader investigation merely because the
coverage model is non-exhaustive.
