---
name: synapse-code-context
description: Navigate and understand codebases with fresh Synapse structural context. Use for code exploration, architecture questions, execution flows, impact analysis, symbol lookup, definitions, and references before falling back to grep or whole-file reads.
---

<!-- SYNAPSE MANAGED SKILL -->

# Synapse Code Context

Use Synapse as the first source of code structure, and as a replacement for initial
shell exploration rather than an addition to it. Two bounded calls answer most code
questions: one orientation, one batch inspection. Both initialize the workspace
automatically — no setup call is needed.

Synapse supplies deterministic structural evidence. You supply the planning, the
semantic interpretation, and the synthesis.

## 1. Plan the evidence facets first

Before retrieving anything, turn the task into a short internal checklist of the concrete
facts the answer needs — for example: entrypoint, configuration, policy, connection,
registration, invocation, persistence, error handling, invalidation.

Keep the checklist small and specific to the request. It is your planning device, not a
Synapse parameter: nothing about it is sent to the server.

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

Call `synapse_inspect(symbols=[...])` once. Normally select 2-4 handles, not the maximum
of eight. Choose them to cover *different* facets rather than four views of one thing:

- the entrypoint or boundary;
- the central implementation;
- a bridge into another area;
- the runtime operation the task asks about.

Prefer production declarations over tests unless the task is explicitly about test
behaviour. Each symbol returns its definition with `file:line`, a bounded source slice,
parent and children, and four relation groups — `callers`, `callees`, `refs_in`,
`refs_out` — carrying stored resolution (`exact|scoped|unique-name|ambiguous|unresolved`),
confidence, and usage kind verbatim, plus unresolved hypotheses.

A second inspection is justified only when a *named* facet is still missing and the
orientation already supplied a relevant handle for it. It must not repeat handles from
the first call.

## 4. Keep an evidence ledger

After inspection, mark every facet on your checklist:

- `verified` — supported by returned source or a correctly calibrated relation;
- `partial` — relevant evidence exists, but a specific fact is truncated, unsupported, or
  unresolved;
- `missing` — no relevant indexed evidence came back.

Treat returned source slices as read. Do not reread the same file ranges to feel more
certain or to restate line citations you already have.

Read relation trust conservatively:

- `exact` and `scoped` are index-local syntactic and structural evidence, not compiler or
  runtime proof;
- `unique-name` is heuristic;
- `ambiguous` and `unresolved` are hypotheses, not confirmed relations;
- empty `callers`/`callees` proves only that no indexed call site was established under
  the reported language coverage.

## 5. Close only explicit gaps

Grep, ripgrep, and file reads are permitted when they close a facet you recorded as
`partial` or `missing`, such as:

- a source slice truncates the exact implementation the answer needs;
- generated or unsupported syntax falls outside index coverage;
- an exact string or configuration value is required;
- evidence names a required cross-cutting file that has no usable declaration handle.

`payload_complete: false`, an omitted relation count, or a non-exhaustive coverage model
is not by itself permission to search broadly. It matters only when the omitted evidence
could change a facet you actually need.

After a successful inspection:

- do not run repository-wide `rg`, `grep`, `find`, or file enumeration;
- do not reread every file the payload named;
- do not repeat the whole Synapse investigation with shell tools for reassurance;
- use the narrowest exact read that closes the recorded gap.

## 6. Stop deliberately

Stop exploring once every requested facet is either verified or explicitly reported as
partial or missing. Do not pursue exhaustive repository coverage the task never asked
for.

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
