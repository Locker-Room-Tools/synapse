"""Parameterized contract tests covering every supported adapter.

The expected paths and MCP entries below are written out literally rather than
derived from the registry, so a registry typo cannot make these tests agree with
themselves. ``test_matrix_covers_every_adapter`` fails when an adapter is added
without an entry here.
"""

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from synapse.cli.adapters import (
    ADAPTERS,
    BEGIN_MARKER,
    ConfigFormat,
    adapter_choices,
    get_adapter,
    install_global_instruction,
    install_global_skill,
    install_instruction_snippet,
    install_project_skill,
    remove_global_instruction,
    remove_instruction_snippet,
    resolve_global_instruction_path,
    resolve_global_skill_path,
    resolve_instruction_path,
    resolve_project_skill_path,
    resolve_user_path,
)
from synapse.cli.config_codecs import loads, read_entry
from synapse.cli.installer import (
    config_has_mcp_server,
    install_mcp_server,
    resolve_config_path,
    uninstall_mcp_server,
)

from .conftest import adapter_path_specs, adapter_project_specs, adapter_user_specs

SERVE_ARGS = ["serve"]

# adapter id -> (project mcp, user mcp, project instructions, global instructions,
#                project skill, global skill). None means the capability is unsupported.
EXPECTED_PATHS: dict[str, tuple[str | None, ...]] = {
    "claude-code": (
        ".mcp.json",
        ".claude.json",
        "CLAUDE.md",
        ".claude/CLAUDE.md",
        None,
        ".claude/skills/synapse-code-context",
    ),
    "codex": (
        ".codex/config.toml",
        ".codex/config.toml",
        "AGENTS.md",
        ".codex/AGENTS.md",
        None,
        ".codex/skills/synapse-code-context",
    ),
    "opencode": (
        "opencode.json",
        ".config/opencode/opencode.json",
        "AGENTS.md",
        ".config/opencode/AGENTS.md",
        None,
        ".config/opencode/skills/synapse-code-context",
    ),
    "hermes": (
        None,
        ".hermes/config.yaml",
        ".hermes.md",
        None,
        None,
        ".hermes/skills/synapse-code-context",
    ),
    "gemini": (
        ".gemini/settings.json",
        ".gemini/settings.json",
        "GEMINI.md",
        ".gemini/GEMINI.md",
        ".gemini/skills/synapse-code-context",
        ".gemini/skills/synapse-code-context",
    ),
    "copilot": (
        ".github/mcp.json",
        ".copilot/mcp-config.json",
        ".github/copilot-instructions.md",
        ".copilot/copilot-instructions.md",
        ".github/skills/synapse-code-context",
        ".copilot/skills/synapse-code-context",
    ),
    "cursor": (
        ".cursor/mcp.json",
        ".cursor/mcp.json",
        ".cursor/rules/synapse.mdc",
        None,
        ".cursor/skills/synapse-code-context",
        ".cursor/skills/synapse-code-context",
    ),
    "windsurf": (
        None,
        ".codeium/windsurf/mcp_config.json",
        ".windsurf/rules/synapse.md",
        ".codeium/windsurf/memories/global_rules.md",
        ".windsurf/skills/synapse-code-context",
        ".codeium/windsurf/skills/synapse-code-context",
    ),
    "cline": (
        ".cline/mcp.json",
        ".cline/mcp.json",
        ".cline/rules/synapse.md",
        ".cline/data/settings/rules/synapse.md",
        ".cline/skills/synapse-code-context",
        ".cline/data/settings/skills/synapse-code-context",
    ),
    "kiro": (
        ".kiro/settings/mcp.json",
        ".kiro/settings/mcp.json",
        ".kiro/steering/synapse.md",
        ".kiro/steering/synapse.md",
        ".kiro/skills/synapse-code-context",
        ".kiro/skills/synapse-code-context",
    ),
    "qwen": (
        ".qwen/settings.json",
        ".qwen/settings.json",
        "QWEN.md",
        ".qwen/QWEN.md",
        ".qwen/skills/synapse-code-context",
        ".qwen/skills/synapse-code-context",
    ),
    "continue": (
        ".continue/mcpServers/synapse.yaml",
        ".continue/config.yaml",
        ".continue/rules/synapse.md",
        None,
        None,
        None,
    ),
}

EXPECTED_ENTRIES: dict[str, dict[str, Any]] = {
    "claude-code": {"command": "synapse", "args": SERVE_ARGS},
    "codex": {"command": "synapse", "args": SERVE_ARGS},
    "opencode": {"type": "local", "enabled": True, "command": ["synapse", "serve"]},
    "hermes": {"command": "synapse", "args": SERVE_ARGS},
    "gemini": {"command": "synapse", "args": SERVE_ARGS},
    "copilot": {
        "type": "local",
        "tools": ["*"],
        "command": "synapse",
        "args": SERVE_ARGS,
    },
    "cursor": {"type": "stdio", "command": "synapse", "args": SERVE_ARGS},
    "windsurf": {"command": "synapse", "args": SERVE_ARGS},
    "cline": {"command": "synapse", "args": SERVE_ARGS},
    "kiro": {"command": "synapse", "args": SERVE_ARGS},
    "qwen": {"command": "synapse", "args": SERVE_ARGS},
    "continue": {"name": "synapse", "command": "synapse", "args": SERVE_ARGS},
}

AGENTS = sorted(EXPECTED_PATHS)

# Unrelated content seeded before install, per (format, shape) combination.
_SEEDS: dict[str, str] = {
    "json": json.dumps({"unrelatedTopLevel": "keep me"}, indent=2) + "\n",
    "toml": '[unrelated]\nkeep = true\n\n[mcp_servers.other]\ncommand = "other-mcp"\n',
    "yaml-mapping": "# a comment worth keeping\nmodel: hermes-4\nshared: &gh gh-mcp\n"
    "mcp_servers:\n  github:\n    command: *gh\n",
    "yaml-list": "name: My Config\nversion: 1.0.0\nschema: v1\n"
    "mcpServers:\n  - name: other\n    command: other-mcp\n",
}


def _seed_key(agent: str) -> str:
    mcp = get_adapter(agent).mcp
    if mcp.fmt is ConfigFormat.YAML:
        return f"yaml-{mcp.shape.value}"
    return mcp.fmt.value


def _seed_config(agent: str, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _SEEDS[_seed_key(agent)]
    path.write_text(text, encoding="utf-8")
    return text


def _read_entry(agent: str, path: Path) -> Any:
    mcp = get_adapter(agent).mcp
    text = path.read_text(encoding="utf-8")
    if mcp.fmt is ConfigFormat.TOML:
        parsed: Any = tomllib.loads(text)
        for key in mcp.key_path:
            parsed = parsed[key]
        return parsed["synapse"]
    return read_entry(loads(mcp.fmt, text, str(path)), mcp)


def _scopes(agent: str) -> tuple[str, ...]:
    mcp = get_adapter(agent).mcp
    scopes = []
    if mcp.project is not None:
        scopes.append("project")
    if mcp.user is not None:
        scopes.append("user")
    return tuple(scopes)


def test_matrix_covers_every_adapter() -> None:
    """A new adapter cannot be registered without expectations in this file."""
    assert set(EXPECTED_PATHS) == set(ADAPTERS)
    assert set(EXPECTED_ENTRIES) == set(ADAPTERS)
    assert set(adapter_choices()) == set(ADAPTERS)


@pytest.mark.parametrize("agent", AGENTS)
def test_project_mcp_path(agent: str, tmp_path: Path) -> None:
    """Project MCP config resolves to the documented workspace-relative path."""
    expected = EXPECTED_PATHS[agent][0]
    adapter = get_adapter(agent)
    if expected is None:
        assert adapter.mcp.project is None
        with pytest.raises(ValueError, match="does not support project-scope"):
            resolve_config_path(adapter, tmp_path, "project")
        return
    assert resolve_config_path(adapter, tmp_path, "project") == tmp_path / expected


@pytest.mark.parametrize("agent", AGENTS)
def test_user_mcp_path(agent: str, isolated_home: Path, tmp_path: Path) -> None:
    """User MCP config resolves to the documented home-relative path."""
    expected = EXPECTED_PATHS[agent][1]
    adapter = get_adapter(agent)
    assert expected is not None
    assert resolve_config_path(adapter, tmp_path, "user") == isolated_home / expected


@pytest.mark.parametrize("agent", AGENTS)
def test_instruction_and_skill_paths(agent: str, isolated_home: Path, tmp_path: Path) -> None:
    """Instruction and skill targets resolve to their documented locations."""
    _, _, project_rules, global_rules, project_skill, global_skill = EXPECTED_PATHS[agent]
    adapter = get_adapter(agent)

    if project_rules is None:
        assert adapter.project_instructions is None
    else:
        assert resolve_instruction_path(agent, tmp_path) == tmp_path / project_rules
    if global_rules is None:
        assert adapter.global_instructions is None
    else:
        assert resolve_global_instruction_path(agent) == isolated_home / global_rules
    if project_skill is None:
        assert adapter.project_skill is None
    else:
        assert resolve_project_skill_path(agent, tmp_path) == tmp_path / project_skill
    if global_skill is None:
        assert adapter.global_skill is None
    else:
        assert resolve_global_skill_path(agent) == isolated_home / global_skill


@pytest.mark.parametrize("agent", AGENTS)
def test_env_home_override_moves_only_declared_paths(
    agent: str,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A home override relocates exactly the paths that declare it."""
    adapter = get_adapter(agent)
    specs = adapter_user_specs(adapter)
    overridden = [spec for spec in specs if spec.env_var]
    if not overridden:
        pytest.skip(f"{agent} declares no home override")

    override_root = tmp_path / "override-home"
    for spec in overridden:
        monkeypatch.setenv(spec.env_var or "", str(override_root))

    for spec in specs:
        resolved = resolve_user_path(spec)
        if spec.env_var:
            assert resolved.is_relative_to(override_root), spec.path
            assert spec.env_prefix is not None
            assert resolved == override_root / spec.path.removeprefix(spec.env_prefix)
        else:
            assert resolved.is_relative_to(isolated_home), spec.path


def test_cline_data_dir_does_not_move_its_mcp_config(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CLINE_DATA_DIR replaces ~/.cline/data/ only, never ~/.cline/mcp.json."""
    data_dir = tmp_path / "cline-data"
    monkeypatch.setenv("CLINE_DATA_DIR", str(data_dir))

    assert resolve_config_path(get_adapter("cline"), tmp_path, "user") == (
        isolated_home / ".cline/mcp.json"
    )
    assert resolve_global_skill_path("cline") == data_dir / "settings/skills/synapse-code-context"
    assert resolve_global_instruction_path("cline") == data_dir / "settings/rules/synapse.md"


@pytest.mark.parametrize("agent", AGENTS)
def test_serialized_entry_matches_documented_shape(
    agent: str, isolated_home: Path, tmp_path: Path
) -> None:
    """The installed entry matches each agent's documented stdio payload."""
    scope = _scopes(agent)[0]
    if agent == "continue" and scope == "user":
        pytest.skip("continue user scope requires an existing config")
    target = resolve_config_path(get_adapter(agent), tmp_path, scope)

    install_mcp_server(agent, tmp_path, scope=scope, portable=True)

    assert _read_entry(agent, target) == EXPECTED_ENTRIES[agent]


@pytest.mark.parametrize("agent", AGENTS)
def test_install_preserves_unrelated_config_and_is_idempotent(
    agent: str, isolated_home: Path, tmp_path: Path
) -> None:
    """Installing twice touches only the Synapse entry and then reports unchanged."""
    for scope in _scopes(agent):
        target = resolve_config_path(get_adapter(agent), tmp_path, scope)
        original = _seed_config(agent, target)

        first = install_mcp_server(agent, tmp_path, scope=scope, portable=True)
        second = install_mcp_server(agent, tmp_path, scope=scope, portable=True)

        assert first.action == "updated", (agent, scope)
        assert second.action == "unchanged", (agent, scope)
        assert _read_entry(agent, target) == EXPECTED_ENTRIES[agent]
        text = target.read_text(encoding="utf-8")
        for fragment in _preserved_fragments(agent):
            assert fragment in text, (agent, scope, fragment)
        assert config_has_mcp_server(agent, tmp_path, scope=scope)
        assert original  # the seed really was written before installing


def _preserved_fragments(agent: str) -> tuple[str, ...]:
    key = _seed_key(agent)
    if key == "json":
        return ("unrelatedTopLevel", "keep me")
    if key == "toml":
        return ("[unrelated]", "other-mcp")
    if key == "yaml-mapping":
        return ("a comment worth keeping", "hermes-4", "&gh gh-mcp", "command: *gh")
    return ("My Config", "other-mcp")


@pytest.mark.parametrize("agent", AGENTS)
def test_install_uninstall_round_trip_restores_original(
    agent: str, isolated_home: Path, tmp_path: Path
) -> None:
    """Uninstall removes only the Synapse entry, leaving the file as it was."""
    for scope in _scopes(agent):
        target = resolve_config_path(get_adapter(agent), tmp_path, scope)
        original = _seed_config(agent, target)

        install_mcp_server(agent, tmp_path, scope=scope, portable=True)
        result = uninstall_mcp_server(agent, tmp_path, scope=scope)
        second = uninstall_mcp_server(agent, tmp_path, scope=scope)

        assert result.action == "removed", (agent, scope)
        assert second.action == "absent", (agent, scope)
        assert target.read_text(encoding="utf-8") == original, (agent, scope)


@pytest.mark.parametrize("agent", AGENTS)
def test_owned_project_config_is_deleted_when_only_synapse_remains(
    agent: str, isolated_home: Path, tmp_path: Path
) -> None:
    """A config file Synapse created is removed again on uninstall."""
    scope = _scopes(agent)[0]
    if agent == "continue" and scope == "user":
        pytest.skip("continue user scope requires an existing config")
    target = resolve_config_path(get_adapter(agent), tmp_path, scope)

    install_mcp_server(agent, tmp_path, scope=scope, portable=True)
    assert target.exists()
    uninstall_mcp_server(agent, tmp_path, scope=scope)

    assert not target.exists(), agent


@pytest.mark.parametrize("agent", AGENTS)
def test_invalid_config_is_reported_without_overwriting(
    agent: str, isolated_home: Path, tmp_path: Path
) -> None:
    """Malformed config raises a clear error and the file is left untouched."""
    scope = _scopes(agent)[0]
    target = resolve_config_path(get_adapter(agent), tmp_path, scope)
    target.parent.mkdir(parents=True, exist_ok=True)
    broken = "{ this is not valid in any supported format ][ \n\tkey = = ="
    target.write_text(broken, encoding="utf-8")

    with pytest.raises((ValueError, tomllib.TOMLDecodeError)):
        install_mcp_server(agent, tmp_path, scope=scope, portable=True)

    assert target.read_text(encoding="utf-8") == broken
    assert not config_has_mcp_server(agent, tmp_path, scope=scope)


@pytest.mark.parametrize("agent", AGENTS)
def test_unsupported_capabilities_raise_capability_specific_errors(
    agent: str, isolated_home: Path, tmp_path: Path
) -> None:
    """Every unsupported capability fails with a message naming the agent."""
    adapter = get_adapter(agent)
    if adapter.global_instructions is None:
        with pytest.raises(ValueError, match="does not support global instructions"):
            install_global_instruction(agent)
    if adapter.global_skill is None:
        with pytest.raises(ValueError, match="does not support global-scope skills"):
            install_global_skill(agent)
    if adapter.project_skill is None:
        with pytest.raises(ValueError, match="does not support project-scope skills"):
            install_project_skill(agent, tmp_path)
    if adapter.project_instructions is None:
        with pytest.raises(ValueError, match="does not support project instructions"):
            install_instruction_snippet(agent, tmp_path)


@pytest.mark.parametrize("agent", AGENTS)
def test_project_instructions_are_marker_owned(
    agent: str, isolated_home: Path, tmp_path: Path
) -> None:
    """Installed instructions carry the ownership marker and uninstall cleanly."""
    adapter = get_adapter(agent)
    if adapter.project_instructions is None:
        pytest.skip(f"{agent} has no project instructions")
    path = resolve_instruction_path(agent, tmp_path)

    created = install_instruction_snippet(agent, tmp_path)
    unchanged = install_instruction_snippet(agent, tmp_path)

    assert created.status == "created"
    assert unchanged.status == "unchanged"
    content = path.read_text(encoding="utf-8")
    assert content.count(BEGIN_MARKER) == 1
    assert f"synapse doctor --path . --agent {agent}" in content
    for key, value in adapter.project_instructions.frontmatter:
        assert f"{key}: {value}" in content
        assert content.startswith("---\n")

    removed = remove_instruction_snippet(agent, tmp_path)
    assert removed.status == "removed"
    assert not path.exists()


@pytest.mark.parametrize("agent", AGENTS)
def test_unmanaged_instruction_files_are_protected(
    agent: str, isolated_home: Path, tmp_path: Path
) -> None:
    """Synapse refuses to clobber an instruction file it does not own."""
    adapter = get_adapter(agent)
    if adapter.project_instructions is None:
        pytest.skip(f"{agent} has no project instructions")
    path = resolve_instruction_path(agent, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("hand written rules\n", encoding="utf-8")

    result = remove_instruction_snippet(agent, tmp_path)

    assert result.status in {"absent", "unmanaged"}
    assert path.read_text(encoding="utf-8") == "hand written rules\n"


@pytest.mark.parametrize("agent", AGENTS)
def test_global_instruction_round_trip(agent: str, isolated_home: Path) -> None:
    """Global instructions install, report unchanged, and remove cleanly."""
    if get_adapter(agent).global_instructions is None:
        pytest.skip(f"{agent} has no global instructions")

    created = install_global_instruction(agent, force=True)
    unchanged = install_global_instruction(agent, force=True)
    removed = remove_global_instruction(agent)

    assert created.status == "created"
    assert unchanged.status == "unchanged"
    assert removed.status == "removed"
    assert not resolve_global_instruction_path(agent).exists()


@pytest.mark.parametrize("agent", AGENTS)
def test_default_scope_matches_supported_capabilities(agent: str) -> None:
    """Global-only agents default to user scope, never to an unsupported one."""
    adapter = get_adapter(agent)
    assert adapter.default_scope in {"project", "user"}
    if adapter.mcp.project is None:
        assert adapter.default_scope == "user"
    if adapter.mcp.user is None:
        assert adapter.default_scope == "project"


@pytest.mark.parametrize("agent", AGENTS)
def test_path_specs_declare_consistent_env_prefixes(agent: str) -> None:
    """Every override-aware path starts with the prefix the variable replaces."""
    adapter = get_adapter(agent)
    for spec in adapter_path_specs(adapter):
        if spec.env_var is None:
            assert spec.env_prefix is None, spec.path
            continue
        assert spec.env_prefix is not None, spec.path
        assert spec.path.startswith(spec.env_prefix), spec.path
    for spec in adapter_user_specs(adapter):
        assert spec.path.startswith("~/"), spec.path
    for spec in adapter_project_specs(adapter):
        assert not spec.path.startswith(("~", "/")), spec.path
        assert spec.env_var is None, spec.path


def test_continue_user_install_requires_an_existing_config(
    isolated_home: Path, tmp_path: Path
) -> None:
    """Continue config.yaml has mandatory keys, so Synapse never creates it."""
    target = resolve_config_path(get_adapter("continue"), tmp_path, "user")

    with pytest.raises(ValueError, match="requires an existing"):
        install_mcp_server("continue", tmp_path, scope="user", portable=True)

    assert not target.exists()


def test_continue_user_uninstall_keeps_the_user_config_file(
    isolated_home: Path, tmp_path: Path
) -> None:
    """Uninstall removes the Synapse list entry but never deletes config.yaml."""
    target = resolve_config_path(get_adapter("continue"), tmp_path, "user")
    original = _seed_config("continue", target)

    install_mcp_server("continue", tmp_path, scope="user", portable=True)
    uninstall_mcp_server("continue", tmp_path, scope="user")

    assert target.exists()
    assert target.read_text(encoding="utf-8") == original
