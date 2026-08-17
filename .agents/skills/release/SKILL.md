---
name: release
description: Cut a Synapse release matching the tag-driven GitHub workflow — changelog section, version bump, uv.lock, tag gates. Use when asked to release, publish, bump the version, or prepare a release PR.
---

# Cut a release

Releases are tag-driven: pushing `vX.Y.Z` runs `.github/workflows/release.yml`
(test → build → PyPI trusted publishing → GitHub release). Every gate below runs *after* the tag
exists, so check them all locally first — a failed tag build means deleting and re-tagging.

## 1. Preconditions

- On up-to-date `main`, working tree clean, CI green.
- `CHANGELOG.md` has a non-empty `## [Unreleased]` section covering everything user-visible
  since the last release. If it is empty, reconstruct it from `git log <last-tag>..HEAD` before
  proceeding — the workflow hard-fails without release notes.

## 2. Steps

1. Move `## [Unreleased]` content into a new `## [X.Y.Z] - YYYY-MM-DD` section (Keep a
   Changelog format), leaving an empty `[Unreleased]` behind.
2. Bump `version` in `pyproject.toml` and regenerate the lock: `uv lock`. Both files must land
   in the release commit — a stale `uv.lock` has required a hotfix release before.
3. Commit as `feat: release Synapse X.Y.Z` with a bulleted body summarizing the release.
4. If releasing via PR, merge with `gh pr merge --rebase` (project preference; do not create
   merge commits).
5. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.

## 3. Workflow gates to pre-check before tagging

- **Tag/version match** — the tag must equal the `pyproject.toml` version exactly.
- **Release notes extraction** — the workflow awk-extracts the `## [X.Y.Z]` section from
  `CHANGELOG.md` and exits 1 if the section is missing or empty. (Known precedent: the 0.5.5
  release commit omitted its section, which would fail `v0.5.5` — add the section before
  tagging.)
- **Wheel content gate** — the built wheel must contain ≥100 `.scm` query files, the adapter
  snippet files, and `skills/synapse-code-context/SKILL.md`. Anything packaged must live under
  `src/synapse/`.
- **Fresh-venv smoke** — `synapse --help`, `install codex --dry-run`, `init --dry-run`,
  `grammars install`, `index .`, `--version` all run in a clean venv.
- **Test job** — ruff check, ruff format --check, mypy, `pytest --cov` (grammars installed
  first).

Local pre-flight:

```bash
uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest -q --cov=synapse
```

## 4. Version references

Only `pyproject.toml` and `uv.lock` carry the version. `src/synapse/__init__.py` reads
`__version__` from distribution metadata, and provenance, watch state, `--version`, and doctor
all consume that. Never grep-replace version strings in `src/` or `docs/` — there are none.
