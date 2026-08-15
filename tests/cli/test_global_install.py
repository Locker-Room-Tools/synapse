"""Tests for one-time global agent installation."""

import hashlib
import json
import tomllib
from pathlib import Path

import pytest

from synapse.cli import main as cli_main
from synapse.cli.adapters import (
    MANAGED_SKILL_MANIFEST,
    install_global_instruction,
    install_global_skill,
    remove_global_skill,
    render_mcp_config,
    resolve_global_instruction_path,
    resolve_global_skill_path,
)
from synapse.cli.installer import install_mcp_server


def _read_skill_manifest(target: Path) -> dict[str, object]:
    return json.loads((target / MANAGED_SKILL_MANIFEST).read_text(encoding="utf-8"))


def _write_skill_manifest(target: Path, payload: object) -> None:
    (target / MANAGED_SKILL_MANIFEST).write_text(
        f"{json.dumps(payload, sort_keys=True)}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("agent", "instruction", "skill"),
    [
        ("codex", ".codex/AGENTS.md", ".codex/skills/synapse-code-context"),
        ("claude-code", ".claude/CLAUDE.md", ".claude/skills/synapse-code-context"),
        (
            "opencode",
            ".config/opencode/AGENTS.md",
            ".config/opencode/skills/synapse-code-context",
        ),
    ],
)
def test_global_agent_paths_are_adapter_specific(
    isolated_home: Path,
    agent: str,
    instruction: str,
    skill: str,
) -> None:
    """Each adapter writes only to its documented user-level locations."""
    assert resolve_global_instruction_path(agent) == isolated_home / instruction
    assert resolve_global_skill_path(agent) == isolated_home / skill


@pytest.mark.parametrize("agent", ["codex", "claude-code", "opencode"])
def test_portable_mcp_config_uses_installed_command_without_workspace(
    tmp_path: Path,
    agent: str,
) -> None:
    """Global configs remain valid outside the build checkout and across repositories."""
    content = render_mcp_config(None, agent_id=agent)

    assert str(tmp_path) not in content
    assert "--workspace" not in content
    assert "-m" not in content
    if agent == "codex":
        server = tomllib.loads(content)["mcp_servers"]["synapse"]
        assert server == {"command": "synapse", "args": ["serve"]}
    else:
        payload = json.loads(content)
        if agent == "claude-code":
            assert payload["mcpServers"]["synapse"] == {
                "command": "synapse",
                "args": ["serve"],
            }
        else:
            assert payload["mcp"]["synapse"]["command"] == ["synapse", "serve"]


@pytest.mark.parametrize("agent", ["codex", "claude-code", "opencode"])
def test_global_instruction_and_skill_are_idempotent(
    isolated_home: Path,
    agent: str,
) -> None:
    """Repeated installs neither duplicate managed instructions nor skill files."""
    instruction = install_global_instruction(agent)
    skill = install_global_skill(agent)
    manifest_before = (skill.path / MANAGED_SKILL_MANIFEST).read_bytes()
    second_instruction = install_global_instruction(agent)
    second_skill = install_global_skill(agent)

    instruction_text = instruction.path.read_text(encoding="utf-8")
    skill_text = (skill.path / "SKILL.md").read_text(encoding="utf-8")
    assert instruction.status == "created"
    assert skill.status == "created"
    assert second_instruction.status == "unchanged"
    assert second_skill.status == "unchanged"
    assert (skill.path / MANAGED_SKILL_MANIFEST).read_bytes() == manifest_before
    assert instruction_text.count("## Synapse code context") == 1
    assert "<!--" not in instruction_text
    assert "synapse_orient" in instruction_text
    assert "synapse_inspect" in instruction_text
    assert "exact text" in instruction_text
    assert "<!--" not in skill_text
    assert "managed-by" not in skill_text
    assert "synapse_orient" in skill_text
    assert "synapse_inspect" in skill_text
    assert "Default workflow" in skill_text
    assert "empty relations != proof of absence" in skill_text
    assert (skill.path / "references" / "evidence-semantics.md").exists()
    manifest = _read_skill_manifest(skill.path)
    assert manifest["managed_by"] == "synapse"
    assert manifest["schema_version"] == 1
    files = manifest["files"]
    assert isinstance(files, dict)
    for relative, digest in files.items():
        assert isinstance(relative, str)
        assert isinstance(digest, str)
        assert digest == hashlib.sha256((skill.path / relative).read_bytes()).hexdigest()


def test_global_skill_refuses_unmanaged_conflict_without_force(
    isolated_home: Path,
) -> None:
    """A user-owned skill is protected unless adoption is explicit."""
    target = resolve_global_skill_path("codex")
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "# My skill\n\nDocument `managed-by: synapse` as an example.\n",
        encoding="utf-8",
    )

    with pytest.raises(FileExistsError, match="unmanaged skill"):
        install_global_skill("codex")

    result = install_global_skill("codex", force=True)

    assert result.status == "updated"
    installed = (target / "SKILL.md").read_text(encoding="utf-8")
    assert "managed-by" not in installed
    assert (target / MANAGED_SKILL_MANIFEST).is_file()


def test_global_skill_updates_an_unchanged_manifest_owned_install(isolated_home: Path) -> None:
    """A valid manifest authorizes replacing an older unmodified managed bundle."""
    target = resolve_global_skill_path("claude-code")
    target.mkdir(parents=True)
    old_content = b"---\nname: synapse-code-context\n---\n\n# Old workflow\n"
    (target / "SKILL.md").write_bytes(old_content)
    _write_skill_manifest(
        target,
        {
            "managed_by": "synapse",
            "schema_version": 1,
            "files": {"SKILL.md": hashlib.sha256(old_content).hexdigest()},
        },
    )

    result = install_global_skill("claude-code")

    assert result.status == "updated"
    installed = (target / "SKILL.md").read_text(encoding="utf-8")
    assert "# Old workflow" not in installed
    assert "synapse_orient" in installed
    files = _read_skill_manifest(target)["files"]
    assert isinstance(files, dict)
    assert set(files) == {
        "SKILL.md",
        "references/evidence-semantics.md",
    }


def test_global_skill_protects_untracked_file_at_a_new_managed_path(
    isolated_home: Path,
) -> None:
    """An update cannot adopt a colliding untracked file without explicit force."""
    install_global_skill("codex")
    target = resolve_global_skill_path("codex")
    manifest = _read_skill_manifest(target)
    files = manifest["files"]
    assert isinstance(files, dict)
    files.pop("agents/openai.yaml")
    _write_skill_manifest(target, manifest)
    collision = target / "agents" / "openai.yaml"
    collision.write_text("user-owned: true\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="untracked files at new managed paths"):
        install_global_skill("codex")

    assert collision.read_text(encoding="utf-8") == "user-owned: true\n"

    result = install_global_skill("codex", force=True)

    assert result.status == "updated"
    assert collision.read_text(encoding="utf-8") != "user-owned: true\n"


@pytest.mark.parametrize("agent", ["codex", "claude-code", "opencode"])
def test_global_skill_files_are_agent_specific(isolated_home: Path, agent: str) -> None:
    """All agents receive references; only Codex receives OpenAI metadata."""
    install_global_skill(agent)

    target = resolve_global_skill_path(agent)
    assert (target / "SKILL.md").exists()
    assert (target / "references" / "evidence-semantics.md").exists()
    manifest = _read_skill_manifest(target)
    files = manifest["files"]
    assert isinstance(files, dict)
    if agent == "codex":
        assert (target / "agents" / "openai.yaml").exists()
        assert set(files) == {
            "SKILL.md",
            "agents/openai.yaml",
            "references/evidence-semantics.md",
        }
    else:
        assert not (target / "agents").exists()
        assert set(files) == {"SKILL.md", "references/evidence-semantics.md"}


def test_global_skill_reinstall_removes_legacy_openai_yaml(isolated_home: Path) -> None:
    """Upgrading a pre-existing managed install drops files no longer owned by the agent."""
    target = resolve_global_skill_path("claude-code")
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "<!-- SYNAPSE MANAGED SKILL -->\n\n# Legacy workflow\n",
        encoding="utf-8",
    )
    legacy = target / "agents" / "openai.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("interface: {}\n", encoding="utf-8")

    result = install_global_skill("claude-code")

    assert result.status == "updated"
    assert not legacy.exists()
    assert not legacy.parent.exists()
    assert (target / "SKILL.md").exists()


def test_global_skill_removal_preserves_untracked_files(isolated_home: Path) -> None:
    """Removal deletes manifest-owned assets while preserving unrelated files."""
    install_global_skill("claude-code")
    target = resolve_global_skill_path("claude-code")
    untracked = target / "notes.md"
    untracked.write_text("Keep me.\n", encoding="utf-8")

    result = remove_global_skill("claude-code")

    assert result.status == "removed"
    assert target.exists()
    assert untracked.read_text(encoding="utf-8") == "Keep me.\n"
    assert not (target / "SKILL.md").exists()
    assert not (target / MANAGED_SKILL_MANIFEST).exists()


def test_global_skill_removal_deletes_a_clean_managed_directory(isolated_home: Path) -> None:
    """A clean manifest-owned bundle is removed completely."""
    install_global_skill("codex")
    target = resolve_global_skill_path("codex")

    result = remove_global_skill("codex")

    assert result.status == "removed"
    assert not target.exists()


@pytest.mark.parametrize("mutation", ["changed", "missing", "symlink"])
def test_modified_managed_skill_fails_closed(
    isolated_home: Path,
    tmp_path: Path,
    mutation: str,
) -> None:
    """Changed manifest-owned files block update and removal without partial writes."""
    install_global_skill("codex")
    target = resolve_global_skill_path("codex")
    skill = target / "SKILL.md"
    if mutation == "changed":
        skill.write_text("# User modification\n", encoding="utf-8")
    elif mutation == "missing":
        skill.unlink()
    else:
        skill.unlink()
        skill.symlink_to(tmp_path / "outside.md")
    manifest_before = (target / MANAGED_SKILL_MANIFEST).read_bytes()

    with pytest.raises(FileExistsError, match="modified managed skill files"):
        install_global_skill("codex")

    assert remove_global_skill("codex").status == "modified"
    assert (target / MANAGED_SKILL_MANIFEST).read_bytes() == manifest_before
    if mutation == "changed":
        assert skill.read_text(encoding="utf-8") == "# User modification\n"
    elif mutation == "missing":
        assert not skill.exists()
    else:
        assert skill.is_symlink()

    recovered = install_global_skill("codex", force=True)

    assert recovered.status == "updated"
    assert skill.is_file()
    assert not skill.is_symlink()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"managed_by": "synapse", "schema_version": 1, "files": {}},
        {
            "managed_by": "synapse",
            "schema_version": 1,
            "files": {"SKILL.md": "not-a-digest"},
        },
        {
            "managed_by": "synapse",
            "schema_version": 1,
            "files": {"SKILL.md": "0" * 64, "../outside": "0" * 64},
        },
        {
            "managed_by": "synapse",
            "schema_version": 1,
            "files": {"SKILL.md": "0" * 64, "unknown.md": "0" * 64},
        },
    ],
)
def test_invalid_skill_manifest_is_never_trusted(
    isolated_home: Path,
    payload: object,
) -> None:
    """Malformed and unsafe manifests grant no update or removal authority."""
    target = resolve_global_skill_path("codex")
    target.mkdir(parents=True)
    skill = target / "SKILL.md"
    skill.write_text("# User skill\n", encoding="utf-8")
    manifest = target / MANAGED_SKILL_MANIFEST
    if payload is None:
        manifest.write_text("{broken", encoding="utf-8")
    else:
        _write_skill_manifest(target, payload)

    with pytest.raises(FileExistsError, match="unmanaged skill"):
        install_global_skill("codex")

    assert remove_global_skill("codex").status == "unmanaged"
    assert skill.read_text(encoding="utf-8") == "# User skill\n"
    assert manifest.exists()


def test_forced_dry_run_does_not_adopt_unmanaged_skill(isolated_home: Path) -> None:
    """A dry-run reports adoption without writing assets or ownership metadata."""
    target = resolve_global_skill_path("codex")
    target.mkdir(parents=True)
    skill = target / "SKILL.md"
    skill.write_text("# User skill\n", encoding="utf-8")

    result = install_global_skill("codex", force=True, dry_run=True)

    assert result.status == "would-update"
    assert skill.read_text(encoding="utf-8") == "# User skill\n"
    assert not (target / MANAGED_SKILL_MANIFEST).exists()


@pytest.mark.parametrize("agent", ["codex", "claude-code", "opencode"])
def test_cli_global_install_creates_portable_managed_assets(
    tmp_path: Path,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agent: str,
) -> None:
    """The canonical command installs config, bootstrap instructions, and skill."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ())
    monkeypatch.setattr(cli_main, "config_has_mcp_server", lambda *args, **kwargs: False)

    exit_code = cli_main.main(["install", agent])

    captured = capsys.readouterr()
    adapter_config_paths = {
        "codex": isolated_home / ".codex" / "config.toml",
        "claude-code": isolated_home / ".claude.json",
        "opencode": isolated_home / ".config" / "opencode" / "opencode.json",
    }
    config_text = adapter_config_paths[agent].read_text(encoding="utf-8")
    assert exit_code == 0
    assert "Synapse installed globally." in captured.out
    assert "Restart " in captured.out
    assert "synapse" in config_text
    assert "serve" in config_text
    assert "--workspace" not in config_text
    assert resolve_global_instruction_path(agent).exists()
    assert (resolve_global_skill_path(agent) / "SKILL.md").exists()


def test_cli_global_install_dry_run_creates_no_home_directories(
    tmp_path: Path,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dry-run resolves all actions without writing or downloading."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ("python",))
    monkeypatch.setattr(
        cli_main,
        "install_grammars",
        lambda: pytest.fail("downloaded grammars"),
    )
    monkeypatch.setattr(cli_main, "config_has_mcp_server", lambda *args, **kwargs: False)

    exit_code = cli_main.main(["install", "codex", "--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "would install 1 missing parsers" in captured.out
    assert "would-create" in captured.out
    assert not isolated_home.exists()


def test_cli_global_install_offline_fails_before_any_partial_write(
    tmp_path: Path,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Offline mode rejects missing grammars before touching global agent state."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ("python",))

    exit_code = cli_main.main(["install", "codex", "--offline"])

    assert exit_code == 2
    assert "Rerun without --offline" in capsys.readouterr().err
    assert not isolated_home.exists()


def test_cli_global_install_no_skill_keeps_config_and_instructions(
    tmp_path: Path,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skill opt-out does not disable the mandatory MCP bootstrap integration."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ())
    monkeypatch.setattr(cli_main, "config_has_mcp_server", lambda *args, **kwargs: False)

    assert cli_main.main(["install", "codex", "--no-skill"]) == 0

    assert (isolated_home / ".codex" / "config.toml").exists()
    assert resolve_global_instruction_path("codex").exists()
    assert not resolve_global_skill_path("codex").exists()


def test_global_uninstall_preserves_user_content_and_project_data(
    tmp_path: Path,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Global uninstall removes owned files only and leaves indexes and prose intact."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ())
    monkeypatch.setattr(cli_main, "config_has_mcp_server", lambda *args, **kwargs: False)
    data_root = tmp_path / "data-root"
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(data_root))
    sentinel = data_root / "workspaces" / "existing" / "index.sqlite"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("index", encoding="utf-8")
    config = isolated_home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('[profile]\nname = "user"\n', encoding="utf-8")
    instructions = isolated_home / ".codex" / "AGENTS.md"
    instructions.write_text("# User rules\n", encoding="utf-8")

    assert cli_main.main(["install", "codex"]) == 0
    skill = resolve_global_skill_path("codex")
    (skill / "notes.md").write_text("user note\n", encoding="utf-8")

    exit_code = cli_main.main(["uninstall", "codex", "--global"])

    assert exit_code == 0
    assert config.read_text(encoding="utf-8") == '[profile]\nname = "user"\n'
    assert instructions.read_text(encoding="utf-8") == "# User rules\n"
    assert (skill / "notes.md").read_text(encoding="utf-8") == "user note\n"
    assert not (skill / "SKILL.md").exists()
    assert sentinel.read_text(encoding="utf-8") == "index"


def test_global_mcp_uninstall_does_not_remove_project_pinned_entry(
    tmp_path: Path,
    isolated_home: Path,
) -> None:
    """Portable uninstall cannot consume a different workspace-pinned user entry."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = install_mcp_server(
        "codex",
        workspace,
        scope="user",
        python_executable="/project-python",
    )
    before = result.path.read_text(encoding="utf-8")

    exit_code = cli_main.main(["uninstall", "codex", "--global", "--path", str(workspace)])

    assert exit_code == 0
    assert result.path.read_text(encoding="utf-8") == before
