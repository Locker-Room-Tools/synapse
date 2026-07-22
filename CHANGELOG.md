# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-07-23

### Added
- Windows support for the detached watch daemon: `synapse watch start` uses
  Windows process-creation flags instead of POSIX sessions on `win32`, and CI
  now runs the full test suite on Windows (Python 3.12).
- Tag-triggered release workflow: pushing a `v*` tag runs the full gate chain,
  verifies the tag matches the project version, rebuilds and smoke-tests the
  wheel, and publishes to PyPI via trusted publishing (OIDC).

### Changed
- Distribution renamed from `synapse-mcp` to `locker-room-tools-synapse-mcp`;
  the import package (`synapse`) and the `synapse` CLI entry point are
  unchanged.
- `core.index` decomposed into focused modules: `index_schema` (DDL,
  connection lifecycle, atomic replacement), `index_writes` (connection-explicit
  writes), `index_queries` (read projections), and `reference_reconciliation`
  (shared by batch indexing and the watch worker). `SymbolIndex` remains as a
  stable facade; no public API changes.
- MCP server startup and watch commands validate the workspace path up front
  (`require_workspace_path`) instead of creating cache state for nonexistent
  directories.
- Qualified names now use each language's native scope separator (`::` for
  C++/CUDA/Crystal/Perl/Ruby/Rust, `\` for PHP) instead of a hardcoded `.`,
  and reference suffix matching follows the same separator.
- The ALL-CAPS-means-constant heuristic is disabled for case-insensitive and
  uppercase-idiomatic languages (Ada, Assembly, COBOL, Erlang, Fortran,
  Pascal, SQL, Verilog, VHDL), where it misclassified ordinary variables.

### Fixed
- Windows CI runs pytest through the synchronized virtual environment directly,
  with fault handling and full interrupt traces enabled.

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
