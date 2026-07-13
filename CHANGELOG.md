# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-07-13

### Added
- FTS5-backed symbol search: prefix matches use a full-text index kept in
  sync by triggers, with a substring fallback; existing databases migrate
  automatically via `PRAGMA user_version`.
- Angular component templates (`*.component.html`) are now detected and
  indexed via the `angular_template` queries.
- `failed_files` count in index stats: unreadable files (dangling symlinks,
  permission errors) are skipped with a warning instead of aborting the run.
- CI (GitHub Actions): lint/type/test matrix on Python 3.12-3.14 plus a
  packaging smoke job that installs the built wheel into a clean venv.

### Fixed
- Wheel now ships tree-sitter query files and adapter data: `queries/` and
  `adapters/` moved into the `synapse` package and are loaded via
  `importlib.resources`. Previous wheels failed with `FileNotFoundError` on
  every parse when installed from PyPI/pip.
- `synapse.__version__` is read from distribution metadata instead of a
  hardcoded, stale string; `synapse doctor` now reports the real version.

### Changed
- Minimum supported Python lowered from 3.14 to 3.12.
- PyPI metadata completed: license (MIT), authors, readme, classifiers,
  project URLs.

## [0.2.4] - 2026-07-12

### Added
- Initial public layout: tree-sitter based indexing across 67 languages,
  SQLite symbol index, 13 MCP tools, polling watch daemon, CLI
  (`index`, `setup`, `serve`, `watch`, `mcp install`, `uninstall`, `doctor`,
  `config`), and adapters for Claude Code, Codex, and OpenCode.
