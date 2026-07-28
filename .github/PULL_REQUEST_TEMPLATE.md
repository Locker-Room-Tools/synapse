## Summary

<!-- What changes, and why. -->

Closes #

## Checklist

- [ ] `uv run ruff check` passes
- [ ] `uv run ruff format --check` passes
- [ ] `uv run mypy` passes (strict, no new errors)
- [ ] `uv run pytest -q --cov=synapse` passes and stays above the 85% coverage floor
- [ ] Tests added or updated under `tests/`, mirroring the package path
- [ ] `CHANGELOG.md` updated under `## [Unreleased]` if the change is user-visible
- [ ] Layering respected: `mcp` may import `core`; `core` never imports `mcp`
- [ ] No network calls added to the index, watch, query, or serving paths

## Notes for reviewers

<!-- Trade-offs, follow-ups, anything worth a closer look. Delete if empty. -->
