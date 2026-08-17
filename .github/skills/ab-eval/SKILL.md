---
name: ab-eval
description: Run a blinded A/B evaluation of a Synapse navigation or instruction change before shipping it. Use when asked to evaluate, compare, or validate a change to orientation, inspection, budgets, instructions, or the managed skill against a baseline.
---

# Blinded A/B evaluation

Navigation-quality changes ship only on evaluation evidence, not on plausibility. This skill
encodes the process refined across iterations 7.x — including the failures that shaped it.

## 1. Build the arms

- Baseline and candidate must be installable side by side and identical except for the single
  change under test. One variable per ablation — bundled changes make a win uninterpretable and
  a loss unattributable.
- Instruction-text changes are ablated the same way as code changes. Lesson on record: a
  plausible-sounding skill-instruction sentence that "obviously helped" failed its blinded
  ablation and was reverted. Plausibility is not evidence.

## 2. Harness

- Runner: Claude Code with Sonnet 5 as the task-executing model.
- Use the self-contained, launch-safe harness pattern: the harness carries its own task set,
  workspace setup, and transcript capture, and must pass its own self-test suite before any
  paid run — a broken harness discovered mid-run wastes the whole run.
- Scoring is a blind judge: it scores transcripts for task accuracy without knowing which arm
  produced them. Arm identity is joined to scores only after judging.
- Task sets must exercise real navigation work (multi-file architecture, flow, impact
  questions), not lookups that any grep answers.

## 3. Metrics

Collect per arm, per task:

- task accuracy (blind-judge score)
- total tokens consumed
- request count — truncation-driven request inflation is a decisive failure signal on its own
  (a run that "wins" on response size but doubles requests is a loss)
- bootstrap/write success where the change touches workspace initialization

## 4. Decision rule

- Ship on a blinded win, or on neutral results with a clear mechanistic rationale for the
  change.
- INCONCLUSIVE (mixed signs, e.g. accuracy down while a secondary metric improves) means
  **diagnose before rerunning** — read the losing transcripts, form a hypothesis, adjust one
  variable. Never rerun unchanged hoping for better variance.
- A loss reverts the change; record the result and the diagnosis so the next iteration starts
  from evidence.

## 5. Hygiene

- Run artifacts (transcripts, scores, harness state) stay out of the repo — never commit `.ai/`
  or evaluation output.
- Record the outcome (win/loss/inconclusive, metric deltas, diagnosis) durably: in the PR
  description of the change under test, or the iteration notes if no PR exists yet.
