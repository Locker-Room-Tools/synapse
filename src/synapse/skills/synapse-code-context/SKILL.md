---
name: synapse-code-context
description: Navigate and understand codebases with fresh Synapse structural context. Use for code exploration, architecture questions, execution flows, impact analysis, symbol lookup, definitions, and references before falling back to grep or whole-file reads.
---

<!-- SYNAPSE MANAGED SKILL -->

# Synapse Code Context

Use Synapse as the first source of code structure. Two bounded calls answer most code
questions: one orientation, one batch inspection. Both initialize the workspace
automatically — no setup call is needed.

## Workflow

1. Translate the user's request into likely repository vocabulary: concrete identifiers,
   file names, and path fragments (translate non-English task words into probable code
   terms). This translation is your job — Synapse evaluates the terms literally.
2. Call `synapse_orient(terms=[...])` with up to 12 terms, or with no terms for a
   repository-map orientation (areas, entrypoints, anchors). The response ranks
   production code first and returns compact handles (`s_...`), match provenance
   (`exact|prefix|substring|path|map`), weak candidates, crowded terms (too generic to
   rank) and unmatched terms, plus coverage counts. Scope with `path_scope` when the
   area is known.
3. Select the handles the task needs and call `synapse_inspect(symbols=[...])` once with
   up to 8 of them. Each symbol returns its definition with `file:line`, a bounded
   source slice, parent/children, and four relation groups — `callers`, `callees`,
   `refs_in`, `refs_out` — carrying stored resolution
   (`exact|scoped|unique-name|ambiguous|unresolved`), confidence, and usage kind
   verbatim, plus unresolved hypotheses.
4. Synthesize the answer yourself from that evidence. Two calls is the normal target;
   a weakly matched orientation may need one more `synapse_orient` with better terms.
5. Read the `coverage` and `budget` blocks: empty or truncated results are never proof
   of absence, and a complete payload is not a claim that the evidence is complete. Use
   grep or file reads only for exact text, generated files, unsupported syntax, or gaps
   the coverage block reports.

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
