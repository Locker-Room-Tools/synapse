---
name: synapse-code-context
description: Navigate and understand codebases with fresh Synapse structural context. Use for code exploration, architecture questions, execution flows, impact analysis, symbol lookup, definitions, and references before falling back to grep or whole-file reads.
---

# Synapse Code Context

## Purpose

Use Synapse before shell exploration for repository structure.
Synapse provides structural evidence; you interpret and synthesize it.

Default workflow:
plan → orient → inspect → follow relations → close gaps → check → synthesize

## 1. Plan

Derive one facet from each material clause of the request, including
requested deliverables (risks, recommendations, comparisons), not only
code mechanics.

Keep the facets as a compact internal ledger. Track each facet as
verified, partial, or missing, and record its best evidence (file:line)
when first seen. A verified facet stays closed. The ledger survives
replanning and drives final-answer planning: no facet is silently
dropped.

For cross-cutting questions, prefer anchors from different relevant
architectural layers.

## 2. Orient

Use `synapse_orient` with 4–8 discriminative terms.

Rules:

- terms come from the request, framework, or previous Synapse results;
- don't invent repository symbols;
- don't path-scope prematurely;
- refine once when orientation is weak or crowded;
- when most terms return unmatched, refine with path fragments from
  files already seen, not more invented names.

## 3. Inspect

Inspect 2–3 diverse production anchors with `synapse_inspect`.

Use returned source and relations as evidence, and record each facet's
best evidence (file:line) in the ledger as it arrives.
Prefer production symbols unless tests are relevant.

## 4. Follow relations

Follow returned handles only to close a specific open facet.
Do not inspect the same handle twice.

## 5. Close gaps

Update the ledger: each facet is verified, partial, or missing.

For a partial or missing facet, make one targeted closing attempt:

- the offered continuation token when the claim depends on a truncated
  source slice;
- a relation handle;
- a refined orientation;
- narrow shell fallback.

Then stop investigating that facet and report it verified or unresolved.

## 6. Shell fallback

Use shell only when Synapse cannot establish the required evidence.

Allowed examples:

- read beyond a truncated source slice when no continuation token was
  offered;
- find an exact configuration string or local value;
- inspect generated files or unsupported syntax;
- close a dynamic-dispatch gap that indexed relations cannot establish.

Never:

- start with broad grep;
- reproduce the whole investigation;
- reread ranges Synapse already returned;
- use shell to upgrade heuristic relations into proven relations.

## 7. Evidence semantics

`exact`/`scoped` = structural evidence  
`unique-name` = heuristic  
`ambiguous`/`unresolved` = hypothesis  
empty relations != proof of absence

See [evidence semantics](references/evidence-semantics.md) for coverage and call-kind
details.

## 8. Reliability claims

Assert a failure scenario or risk only when read evidence names:

1. the initiating state or fault;
2. the code path that permits it;
3. the guard, retry, or recovery path that fails to eliminate it;
4. the observable outcome.

If the guard or recovery path is unread — for example the source slice
was truncated before it — fetch the continuation or downgrade the claim
to an unresolved hypothesis.

## 9. Check

Before answering, review the ledger without further tool calls:

- every facet is answered or explicitly unresolved;
- flow stages connect in execution order, not as isolated symbols;
- each claim carries the evidence strength actually held;
- evidence retrieved early is not dropped because later payloads are
  more recent;
- each asserted risk satisfies the reliability chain above.

This check reviews evidence already held; it is not a license to keep
exploring.

## 10. Stop

Stop when every facet is verified or unresolved.

Clearly distinguish:

- verified evidence;
- structural inference;
- unresolved evidence.
