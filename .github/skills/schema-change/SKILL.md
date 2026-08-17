---
name: schema-change
description: Change Synapse index persistence safely — SQLite schema, writer contract, migrations, lifecycle repair. Use when touching core/index (schema.py, writes.py, reads.py), symbol-write invariants, or anything the watch daemon persists.
---

# Change index persistence

Persistence changes ripple through a fixed set of guards. Skipping a step does not fail fast —
it produces stale indexes, daemons writing under an old contract, or repairs that never trigger.

## 1. Which constant moves? (`src/synapse/core/index/contract.py`)

- `SCHEMA_VERSION` — bump when the on-disk shape changes. It also feeds the
  reference-extraction fingerprint (`core/indexing/references.py` hashes
  `f"schema:{SCHEMA_VERSION}"`), so a bump forces a full rebuild on next ensure.
- `INDEX_WRITER_CONTRACT_VERSION` — bump whenever **symbol-write invariants** change, even with
  no schema change. The package version is not enough: two development builds share one version,
  and a running watch daemon built from older code would otherwise keep writing under the old
  contract. The module docstring lists the current contract's invariants (every symbol row
  carries its handle written in the same statement; file symbol replacement is
  delete-then-insert in one transaction; handles are unique) — extend that list when the
  contract grows.

When in doubt, bump the writer contract; a spurious bump costs one daemon restart, a missed one
corrupts evidence silently.

## 2. Schema and write path

- `src/synapse/core/index/schema.py` — the SQL, the `_migrate_columns` tables, and
  `_rebuild_symbols_table`'s copy-column list. The `CHECK` constraint and `SCHEMA` "must never
  drift" (comment in file). Rebuilds use explicit statements in one transaction — never
  `executescript` mid-rebuild — so a partial rebuild cannot commit. Migrations must be
  re-runnable against an already-migrated database.
- Then `writes.py` / `reads.py`, and `handles.py` if handle shape is involved.

## 3. Consumers to verify by reading, not assuming

- `core/provenance.py` — `runtime_provenance()` payload reports both versions.
- `core/watch/state.py` — daemon status mismatch check and the stamped `contract_version`.
- `core/watch/daemon.py` — the mismatch error message.
- `core/lifecycle.py` — the `writer-contract-mismatch` repair reason; a mismatched daemon must
  be stopped **before** any repair touches the database.

## 4. Test gate

```bash
uv run pytest -q tests/core/index tests/core/watch tests/core/test_lifecycle.py tests/core/test_provenance.py tests/core/test_navigation_readiness.py
```

plus the slow multi-process suite (spawns real interpreters):

```bash
uv run pytest -q tests/core/test_multiprocess_rebuild.py
```

Key suites and what they pin: `tests/core/index/test_writer_contract.py` (constant + invariant
list), `test_schema_migration.py`, `test_handle_integrity.py`,
`tests/core/watch/test_writer_contract.py`, `tests/core/test_lifecycle.py` (every repair trigger
has a named reason; stale daemon stopped before rebuild), `test_navigation_readiness.py`
(healthy path stays read-only and cheap), `test_multiprocess_rebuild.py` (stale fingerprint
rebuilds exactly once under concurrency).

## 5. Docs

Update the invariant/bump-rule prose in `AGENTS.md` and `docs/architecture.md` if the rule
itself changed, and add a `CHANGELOG.md` `[Unreleased]` entry for anything user-visible
(a forced rebuild counts as user-visible).
