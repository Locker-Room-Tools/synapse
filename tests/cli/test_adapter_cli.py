"""CLI behaviour for the expanded adapter set."""

from pathlib import Path

import pytest

from synapse.cli import main as cli_main
from synapse.cli.adapters import (
    ADAPTERS,
    get_adapter,
    install_project_skill,
    resolve_global_skill_path,
)
from synapse.cli.installer import install_mcp_server, resolve_config_path

GLOBAL_ONLY = [agent for agent, adapter in ADAPTERS.items() if adapter.mcp.project is None]
PROJECT_CAPABLE = [agent for agent, adapter in ADAPTERS.items() if adapter.mcp.project is not None]


@pytest.fixture(autouse=True)
def _no_grammar_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_main, "missing_grammars", lambda: ())
    monkeypatch.setattr(cli_main, "config_has_mcp_server", lambda *a, **k: False)
    monkeypatch.setattr(
        cli_main,
        "install_grammars",
        lambda: pytest.fail("install must not download grammars in these tests"),
    )


def test_every_adapter_appears_in_help(capsys: pytest.CaptureFixture[str]) -> None:
    """All supported adapter ids are offered by the install command."""
    with pytest.raises(SystemExit):
        cli_main.main(["install", "--help"])

    output = capsys.readouterr().out
    for agent in ADAPTERS:
        assert agent in output


def test_unknown_agent_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    """An unsupported agent id fails argparse validation."""
    with pytest.raises(SystemExit):
        cli_main.main(["install", "not-a-real-agent"])

    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize("agent", sorted(ADAPTERS))
def test_install_dry_run_writes_nothing(
    agent: str,
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A dry-run install previews every artifact without creating a home."""
    monkeypatch.chdir(tmp_path)
    if agent == "continue":
        pytest.skip("continue user scope requires an existing config")

    assert cli_main.main(["install", agent, "--dry-run", "--offline"]) == 0

    output = capsys.readouterr().out
    assert "MCP config:" in output
    assert "Global instructions:" in output
    assert "Global skill:" in output
    assert not isolated_home.exists()


@pytest.mark.parametrize("agent", sorted(ADAPTERS))
def test_install_reports_unsupported_capabilities_instead_of_failing(
    agent: str,
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Capabilities an agent lacks are skipped cleanly and named in the output."""
    monkeypatch.chdir(tmp_path)
    if agent == "continue":
        pytest.skip("continue user scope requires an existing config")
    adapter = get_adapter(agent)

    cli_main.main(["install", agent, "--dry-run", "--offline"])

    output = capsys.readouterr().out
    if adapter.global_instructions is None:
        assert f"Global instructions: not supported by {adapter.display_name}" in output
    if adapter.global_skill is None:
        assert "Global skill: not supported" in output


@pytest.mark.parametrize("agent", sorted(ADAPTERS))
def test_install_and_uninstall_round_trip_through_cli(
    agent: str,
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real global install creates its artifacts and uninstall removes them."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data"))
    if agent == "continue":
        pytest.skip("continue user scope requires an existing config")
    adapter = get_adapter(agent)
    config_path = (
        resolve_config_path(adapter, tmp_path, "user") if adapter.mcp.user is not None else None
    )

    assert cli_main.main(["install", agent, "--offline"]) == 0
    if config_path is not None:
        assert config_path.exists(), agent
    if adapter.global_skill is not None:
        assert (resolve_global_skill_path(agent) / "SKILL.md").exists()

    assert cli_main.main(["uninstall", agent, "--global"]) == 0
    if config_path is not None:
        assert not config_path.exists(), agent
    if adapter.global_skill is not None:
        assert not resolve_global_skill_path(agent).exists()


def test_shared_global_skill_survives_until_last_agent(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Goose and Zed share ~/.agents/skills; the skill outlives the first uninstall."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data"))
    assert cli_main.main(["install", "goose", "--offline"]) == 0
    assert cli_main.main(["install", "zed", "--offline"]) == 0
    skill_dir = resolve_global_skill_path("goose")
    assert skill_dir == resolve_global_skill_path("zed")
    capsys.readouterr()

    assert cli_main.main(["uninstall", "goose", "--global"]) == 0
    assert "Global skill kept (still used by Zed)" in capsys.readouterr().out
    assert (skill_dir / "SKILL.md").exists()

    assert cli_main.main(["uninstall", "zed", "--global"]) == 0
    assert "Global skill removed" in capsys.readouterr().out
    assert not skill_dir.exists()


def test_shared_project_skill_survives_until_last_agent(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both Copilot adapters share .github/skills at project scope."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SYNAPSE_DATA_DIR", str(tmp_path / "data"))
    workspace = str(tmp_path)
    for agent in ("copilot", "copilot-vscode"):
        install_mcp_server(agent, tmp_path, scope="project")
        install_project_skill(agent, tmp_path)
    skill_dir = tmp_path / ".github" / "skills" / "synapse-code-context"
    assert (skill_dir / "SKILL.md").exists()

    assert cli_main.main(["uninstall", "copilot", "--path", workspace]) == 0
    assert "Skill kept (still used by GitHub Copilot (VS Code))" in capsys.readouterr().out
    assert (skill_dir / "SKILL.md").exists()

    assert cli_main.main(["uninstall", "copilot-vscode", "--path", workspace]) == 0
    assert "Skill removed" in capsys.readouterr().out
    assert not skill_dir.exists()


@pytest.mark.parametrize("agent", sorted(GLOBAL_ONLY))
def test_setup_rejects_global_only_agents(
    agent: str,
    isolated_home: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Project setup fails loudly rather than writing a global config."""
    adapter = get_adapter(agent)

    assert cli_main.main(["setup", agent, "--path", str(tmp_path), "--dry-run"]) == 2

    error = capsys.readouterr().err
    assert "does not support project-scope MCP config" in error
    assert f"synapse install {adapter.id}" in error
    assert not isolated_home.exists()


@pytest.mark.parametrize("agent", sorted(PROJECT_CAPABLE))
def test_setup_dry_run_previews_project_capabilities(
    agent: str,
    isolated_home: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Setup previews the project MCP, instruction, and skill targets."""
    adapter = get_adapter(agent)

    assert cli_main.main(["setup", agent, "--path", str(tmp_path), "--dry-run"]) == 0

    output = capsys.readouterr().out
    assert str(resolve_config_path(adapter, tmp_path, adapter.default_scope)) in output
    if adapter.project_skill is None:
        assert f"Skill: not supported by {adapter.display_name}" in output
    else:
        assert "Skill: would install at" in output


def test_only_context_injecting_agents_register_a_hook(
    isolated_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The nudge ships only where a hook can add context while allowing the call.

    Gemini has no PreToolUse event, Droid excludes additionalContext from it, and
    Cursor delivers its agent message only on denial, so none of them qualify.
    """
    monkeypatch.chdir(tmp_path)
    expected = ["claude-code", "crush", "qwen"]

    hooked = [
        agent
        for agent in sorted(ADAPTERS)
        if agent != "continue" and _install_mentions_hook(agent, capsys)
    ]

    assert hooked == expected
    assert sorted(a for a, ad in ADAPTERS.items() if ad.hook is not None) == expected


def _install_mentions_hook(agent: str, capsys: pytest.CaptureFixture[str]) -> bool:
    cli_main.main(["install", agent, "--dry-run", "--offline"])
    return "hook" in capsys.readouterr().out.lower()
