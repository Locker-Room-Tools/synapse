# Security Policy

## Supported versions

Synapse is pre-1.0. Only the latest released version on the current `0.x` line receives
security fixes. Upgrade before reporting an issue if you are on an older release.

## Reporting a vulnerability

Report privately — do not open a public issue.

Use [GitHub Security Advisories](https://github.com/Locker-Room-Tools/synapse/security/advisories/new)
("Report a vulnerability" on the repository's Security tab). If you cannot use that, email
<dskrl@outlook.com>.

Please include the Synapse version, your OS and Python version, and the smallest
reproduction you can manage. Expect an initial response within a week.

## Threat model

Synapse indexes source code on the machine it runs on, so the boundaries worth attacking
are narrow and worth stating explicitly:

- **Local-first.** Source code is never uploaded anywhere. There are no network calls in
  the index, watch, query, or MCP serving paths.
- **Explicit downloads only.** The single network operation is `synapse grammars install`,
  which fetches tree-sitter parser binaries on demand. Indexing loads only parsers already
  present in the local cache and reports a setup error instead of downloading implicitly.
- **Local data.** The SQLite index, watch state, and logs live under the workspace data
  directory and the user config directory. Nothing is written outside the workspace and
  those directories.
- **Untrusted input.** Indexed source files, `.synapse/config.json`, and MCP tool arguments
  are all untrusted input. Path traversal, writes outside the workspace, and code execution
  from parsing a file are in scope.

When reporting, say which of those boundaries your finding crosses — it makes triage much
faster.

Out of scope: vulnerabilities in tree-sitter grammars themselves (report those upstream),
and anything that requires an attacker to already have local code-execution as your user.
