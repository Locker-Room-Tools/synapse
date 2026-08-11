---
name: synapse-code-context
description: Navigate and understand codebases with fresh Synapse structural context. Use for code exploration, architecture questions, execution flows, impact analysis, symbol lookup, definitions, and references before falling back to grep or whole-file reads.
---

<!-- SYNAPSE MANAGED SKILL -->

# Synapse Code Context

## Purpose

Use Synapse before shell exploration for repository structure.
Synapse provides structural evidence; you interpret and synthesize it.

Default workflow:
plan → orient → inspect → follow relations → close gaps → synthesize

## 1. Plan

Identify the few concrete facts required by the task.

For cross-cutting questions, prefer anchors from different relevant
architectural layers.

## 2. Orient

Use `synapse_orient` with 4–8 discriminative terms.

Rules:

- terms come from the request, framework, or previous Synapse results;
- don't invent repository symbols;
- don't path-scope prematurely;
- refine once when orientation is weak or crowded.

## 3. Inspect

Inspect 2–3 diverse production anchors with `synapse_inspect`.

Use returned source and relations as evidence.
Prefer production symbols unless tests are relevant.

## 4. Follow relations

Follow returned handles only to close a specific open fact.
Do not inspect the same handle twice.

## 5. Close gaps

For each required fact:

- verified
- unresolved

If evidence is insufficient, make one targeted closing attempt:

- relation handle;
- refined orientation;
- narrow shell fallback.

Then stop investigating that fact.

## 6. Shell fallback

Use shell only when Synapse cannot establish the required evidence.

Allowed examples:

- read beyond a truncated source slice;
- find an exact configuration string or local value;
- inspect generated files or unsupported syntax;
- close a dynamic-dispatch gap that indexed relations cannot establish.

Never:

- start with broad grep;
- reproduce the whole investigation;
- use shell to upgrade heuristic relations into proven relations.

## 7. Evidence semantics

`exact`/`scoped` = structural evidence  
`unique-name` = heuristic  
`ambiguous`/`unresolved` = hypothesis  
empty relations != proof of absence

See [evidence semantics](references/evidence-semantics.md) for coverage and call-kind
details.

## 8. Stop

Stop when every requested fact is verified or unresolved.

Clearly distinguish:

- verified evidence;
- structural inference;
- unresolved evidence.
