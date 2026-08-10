"""Versioned identity of the index storage and persistence contracts.

`SCHEMA_VERSION` describes the shape of the database file. `INDEX_WRITER_CONTRACT_VERSION`
describes the invariants a *writer process* promises to maintain on every incremental
write; bump it whenever those invariants change, even when the schema does not.

The writer contract is a declared integer rather than a fingerprint over the write path:
a fingerprint would also change on comments, formatting, and editable-versus-wheel
packaging, forcing daemon restarts and rebuilds for changes that alter no invariant, and
nobody could read a diff and say why. A package version is not usable either, since two
development builds share one version while implementing different contracts.

Contract 1 invariants:
  - every persisted symbol row carries `symbol_handle(symbol.id)`, written in the same
    statement as the symbol itself;
  - replacing the symbols of a file is delete-then-insert inside one transaction;
  - persisted handles are unique across the symbol table.
"""

SCHEMA_VERSION = 6
INDEX_WRITER_CONTRACT_VERSION = 1
