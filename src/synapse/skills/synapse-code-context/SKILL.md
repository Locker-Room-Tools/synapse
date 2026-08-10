---
name: synapse-code-context
description: Navigate and understand codebases with fresh Synapse structural context. Use for code exploration, architecture questions, execution flows, impact analysis, symbol lookup, definitions, and references before falling back to grep or whole-file reads.
---

<!-- SYNAPSE MANAGED SKILL -->

# Synapse Code Context

Use Synapse as the first source of code structure, and as a replacement for initial
shell exploration rather than an addition to it. Two calls remain the common fast
path, not a cap; up to four bounded calls can be appropriate for a cross-cutting
task. Both tools initialize the workspace automatically — no setup call is needed.

Synapse supplies deterministic structural evidence. You supply the planning, the
semantic interpretation, and the synthesis. The lifecycle:

```text
plan evidence facets
  -> orient
  -> inspect 2-3 initial facet-diverse anchors
  -> follow 1-2 returned relation handles for still-open facets
  -> if necessary, refine orientation or use one facet-scoped shell fallback
  -> verify or report the facet unresolved
  -> synthesize and stop
```

## 1. Plan the evidence facets first

Before retrieving anything, turn the task into a short internal checklist of the concrete
facts the answer needs — for example: entrypoint, configuration, policy, connection,
registration, invocation, persistence, error handling, invalidation.

Keep the checklist small and specific to the request. It is your planning device, not a
Synapse parameter: nothing about it is sent to the server.

For a cross-cutting lifecycle question, plan an anchor set that covers three distinct
altitudes:

1. the subsystem implementation;
2. the host or composition-root integration;
3. a generic dispatcher, executor, or policy boundary.

This applies equally to plugin systems, queues, transports, DI containers, and
framework adapters: the interesting behavior usually lives where a generic mechanism
executes a specific implementation.

## 2. Orient with bounded repository vocabulary

Call `synapse_orient(terms=[...])` with 4-8 discriminative identifiers, file names, or
path fragments. The contract allows 12; more terms usually means vaguer terms.

- Terms must come from the request, from known language/framework vocabulary, or from
  names a previous orientation returned. Never invent a plausible symbol and then present
  it as if the repository contained it.
- Call it with no terms only for a genuinely broad architecture question; that returns a
  repository-map orientation (areas, entrypoints, anchors).
- Use `path_scope` only once the task or returned evidence establishes the subsystem. Do
  not narrow away a cross-cutting facet just because one directory matched strongly.
- The response ranks production code first and returns compact handles (`s_...`), match
  provenance (`exact|prefix|substring|path|map`), weak candidates, crowded terms (too
  generic to rank), unmatched terms, matched files, and coverage counts.

If the orientation is weak, crowded, or misses a facet on your checklist, one refined
`synapse_orient` (better terms or a `path_scope`) is the correct next step. Unmatched
terms alone are not a reason to start a shell search.

## 3. Inspect a small, facet-diverse selection

Call `synapse_inspect(symbols=[...])` with 2-3 initial facet-diverse anchors, not the
maximum of eight. Choose them to cover *different* facets rather than several views of
one thing:

- the entrypoint or boundary;
- the central implementation;
- a bridge into another area;
- the runtime operation the task asks about.

Prefer production declarations over tests unless the task is explicitly about test
behaviour. Each symbol returns its definition with `file:line`, a bounded source slice,
parent and children, and four relation groups — `callers`, `callees`, `refs_in`,
`refs_out` — carrying stored resolution (`exact|scoped|unique-name|ambiguous|unresolved`),
confidence, and usage kind verbatim, plus unresolved hypotheses.

## 4. Follow returned relation handles

For a facet still open after the initial anchors, follow 1-2 returned relation handles
in a follow-up `synapse_inspect`. The handle may come from the original
orientation or from a previous inspection relation: every resolved
`callers`/`callees`/`refs_in`/`refs_out` endpoint carries a compact handle (`h`) that is
a first-class inspection input. Do not resubmit handles you already inspected.

## 5. Keep an attempt-aware evidence ledger

After inspection, mark every facet on your checklist:

- `verified` — supported by returned source or a correctly calibrated relation;
- `partial` — relevant evidence exists, but a specific fact is truncated, unsupported, or
  unresolved;
- `missing` — no relevant indexed evidence came back.

`verified` closes the facet. `partial` or `missing` is not a terminal state: make one
bounded gap-closing attempt — a returned relation handle, one refined orientation, or
one facet-scoped shell fallback — after which the facet is verified or unresolved.
Report unresolved evidence honestly. Do not mark a facet `missing` and stop without
trying an available relation handle or one justified fallback; conversely, do not
repeat attempts after the bounded close fails.

Treat returned source slices as read. Do not reread the same file ranges to feel more
certain or to restate line citations you already have.

Read relation trust conservatively:

- `exact` and `scoped` are index-local syntactic and structural evidence, not compiler or
  runtime proof;
- `unique-name` is heuristic;
- `ambiguous` and `unresolved` are hypotheses, not confirmed relations;
- empty `callers`/`callees` proves only that no indexed call site was established under
  the reported language coverage.

## 6. One disciplined shell fallback

One narrowly scoped search or file read, tied to a named open facet, is justified when
any of these holds:

- dynamic dispatch cannot be established from the indexed AST evidence;
- the needed fact is a local variable or exact configuration string;
- a returned source slice is truncated or budget-shortened;
- coverage explicitly names the required unsupported semantics;
- no usable relation handle was returned.

Restrict the fallback with a discriminative expression and the narrowest known path,
for example:

```bash
rg -n 'runToolUse|checkPermissions|tool\.call' src/relevant-area/
```

Continue a truncated file read from the first line not already returned; do not reread
the prefix merely to recover context already present in the transcript.

What stays out of bounds:

- a broad shell search before using Synapse;
- do not repeat the complete investigation with shell tools for reassurance;
- do not read every file the payload named;
- do not use shell output to silently strengthen ambiguous or heuristic relations.

`payload_complete: false`, an omitted relation count, or a non-exhaustive coverage model
is not by itself permission to search broadly. It matters only when the omitted evidence
could change a facet you actually need.

## 7. Stop deliberately

Stop once every requested facet is verified or explicitly reported unresolved after its
one bounded close attempt. Do not pursue exhaustive repository coverage the task never
asked for.

The final answer must keep three things apart: verified facts, calibrated structural
inference, and missing evidence.

## Response bounds

Both tools bound their own responses server-side and always report `payload_complete` and
`coverage`; there is no budget parameter to raise. Close a gap with a narrower, targeted
call — not with a bigger payload.

Empty or truncated results are never proof of absence, and a complete payload is not a
claim that the evidence is complete.

## Reading callers and callees

`callers` and `callees` contain only sites whose syntax proves a call. Every other
reference — a declared type, a return type, a base class, an attribute or decorator, a
member read — is returned as `refs_in`/`refs_out` with its usage kind, so it is still
visible evidence but is not presented as a call.

An empty `callers` therefore means "no call was proven", not "nothing calls this". Check
`coverage.extraction[].call_kinds`: it lists the usage kinds that prove a call in that
language, and an empty list means the language yields no call evidence at all, so the
relevant usages will be in `refs_in`/`refs_out` instead.

When a symbol has zero incoming references, `coverage.extraction` also calibrates the
other indexed workspace languages, marked `"evidence": false` — a caller could have been
written in any of them, so read the zero against their `call_kinds` and `limitations`
before concluding nothing calls the symbol.

## Freshness and recovery

The navigation tools repair the workspace automatically when it has no index, is
uninitialized or degraded, is missing parsers, or still holds relations built by an older
extractor — so an upgrade costs one rebuild rather than stale answers. If Synapse tools
are unavailable, report that the global integration may need installation or an agent
restart; do not imitate those operations manually. Administrative and primitive tools
(re-indexing, definitions, references, configuration, watch diagnostics) live behind
`synapse serve --profile full`.
