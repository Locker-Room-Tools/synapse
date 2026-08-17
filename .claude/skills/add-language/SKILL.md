---
name: add-language
description: Add tree-sitter language support to Synapse — .scm query pair, LanguageSpec registry entry, tier tests. Use when asked to add, extend, or fix support for a programming language, its file extensions, or its symbol/reference extraction.
---

# Add a language

New languages are added via declarative `.scm` queries plus one registry entry — never via
per-language branching in `src/synapse/core/indexing/parser.py`. If the task seems to need a
parser branch, the design is wrong; stop and reconsider the query or the `LanguageSpec` metadata.

## 1. Grammar precondition

`LanguageSpec.tree_sitter_name` must be a grammar that `tree-sitter-language-pack` ships — the
install path is fully registry-derived (`tree_sitter_language_names()` collects
`spec.tree_sitter_name` values; `install_grammars()` prefetches exactly that set). There is no
separate grammar list to edit. Verify early:

```bash
uv run python -c "from tree_sitter_language_pack import get_language; get_language('<name>')"
```

After registering, `uv run python -m synapse grammars install` must succeed.

## 2. Query pair

Create `src/synapse/queries/<lang>/symbols.scm` and `references.scm`. Both are required and must
be non-empty (`core/languages/queries.py` raises on missing/empty;
`tests/core/languages/test_queries.py` loops over all of `LANGUAGES` asserting `@reference`
appears). Start by copying the nearest-relative language's pair. `query_dir` may point at another
language's directory when the grammar is shared (`tsx` reuses `query_dir="typescript"`).

**symbols.scm** — each match must contain exactly one `@definition.<kind>` capture and one
`@name` capture, or the match is skipped. Valid kinds are the `_CAPTURE_KIND_MAP` set in
`src/synapse/core/indexing/parser.py`:
`class constant constructor enum field function import interface method module namespace package
property record struct type variable` — any other suffix raises. Map onto the
Container/Entity/Worker model: containers = namespace/package/module/class/interface/struct/
record/enum/type; workers = function/method/constructor/property; entities =
field/variable/constant/import. Containment (nesting) is computed from byte ranges, not from the
query — do not try to encode parent/child in the query. ALL-CAPS field/variable captures are
auto-promoted to `constant` when `uppercase_constants=True` (the default).

**references.scm** — capture suffix becomes the stored usage kind via
`suffix.replace("_", "-")` (`@reference.base_type` → `base-type`); a bare `@reference` stores no
usage kind (the `go` style — correct when you cannot distinguish reference positions). Copy the
leading comment convention from `src/synapse/queries/python/references.scm`, which documents
which suffixes are advertised and why only some are call-proven.

## 3. Registry entry

Add one `LanguageSpec(...)` to `LANGUAGES` in `src/synapse/core/languages/registry.py`.
Minimal shape (most languages): `id`, `tree_sitter_name`, `extensions`, `query_dir`, plus
`name_separator` if not `"."` (rust uses `"::"`).

Rules that bite:

- **Extensions are exclusive.** `_build_extension_map` raises on duplicate claims; the collision
  policy comment near the bottom of the file documents the precedents (`.m`→matlab, `.h`→c,
  `.v`→verilog). The losing language must simply not list the extension. Extension-less matching
  goes through `_FILENAME_SUFFIX_TO_LANGUAGE` (see `angular_template`).
- **Call semantics are evidence-based.** `call_usage_kinds` must be a strict subset of
  `reference_usage_kinds`, and every advertised usage kind must appear as
  `@reference.<kind_with_underscores>` in the packaged query (pinned by tests such as
  `test_ts_js_advertised_usage_kinds_are_backed_by_query_captures`). Default both to empty —
  an empty `call_usage_kinds` means the language yields no caller/callee evidence, which is
  honest; a wrong one silently corrupts call-proven navigation. Only advertise a kind as a call
  when the query capture provably matches only call sites (e.g. Python's `invocation`).
- `reference_syntax` (the structural resolver config) exists only for csharp and python; do not
  attempt one for a new language unless that is explicitly the task.

## 4. Tests

There are no on-disk fixtures — samples are inline string literals.

1. Add one tuple `(language, file_name, source, expected_symbol, expected_reference)` to the
   appropriate `TIER*_SAMPLES` table in `tests/core/indexing/test_parser_tier2.py` … `tier5.py`.
   For Tier-1-grade support (rich symbol coverage), write a dedicated
   `test_parse_file_extracts_<lang>_symbols` in `tests/core/indexing/test_parser.py` following
   the existing per-language tests.
2. Add the language to the matching `TIERn_LANGUAGES` list in
   `tests/core/languages/test_queries.py`.
3. Add one `detect_language(Path("x.<ext>")) == "<id>"` line to
   `test_detect_language_by_extension` in `tests/core/languages/test_registry.py`.

## 5. Docs and changelog

- `CONTRIBUTING.md` § "Adding a language" is the canonical human checklist — keep it consistent
  if this skill's procedure and the doc ever diverge.
- Add a `CHANGELOG.md` entry under `## [Unreleased]` (recent entries show the tier-announcement
  wording). There is no per-language table in `docs/` or `README.md` to update.

## 6. Packaging and gate

Query files must live under `src/synapse/` to ship in the wheel — the release workflow gates on
≥100 packaged `.scm` files. Finish with:

```bash
uv run ruff check && uv run mypy && uv run pytest -q tests/core/languages tests/core/indexing
```
