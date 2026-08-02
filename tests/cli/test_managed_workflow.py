"""The managed orchestration contract carried by skill, snippets, and handshake.

These tests pin *behaviour the instructions must describe*, not their wording as a
whole: the canonical skill owns the detailed workflow, and the always-on surfaces stay
short pointers to it. Text is compared whitespace-normalized so reflowing a paragraph
is not a test failure.
"""

from pathlib import Path

import pytest

from synapse.cli.adapters import (
    global_snippet,
    install_global_skill,
    project_snippet,
    resolve_global_skill_path,
)
from synapse.cli.adapters.skills import MANAGED_SKILL_MARKER, SYNAPSE_SKILL
from synapse.mcp.instructions import SERVER_INSTRUCTIONS

SKILL_CONTRACT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("navigation tools", ("synapse_orient", "synapse_inspect")),
    ("facet planning", ("evidence facets", "checklist")),
    ("bounded orientation", ("4-8 discriminative", "The contract allows 12")),
    ("small facet-diverse selection", ("2-4 handles", "not the maximum")),
    ("evidence ledger", ("`verified`", "`partial`", "`missing`")),
    ("no reread", ("Treat returned source slices as read", "Do not reread")),
    ("no broad re-search", ("do not run repository-wide", "narrowest exact read")),
    ("stop rule", ("Stop exploring once every requested facet",)),
    ("default budget", ("no budget parameter to raise",)),
)

SNIPPET_CONTRACT: tuple[str, ...] = (
    "synapse_orient",
    "synapse_inspect",
    "4-8 discriminative",
    "2-4 handles",
    "no budget parameter to raise",
    "never proof of absence",
    "named partial/missing facet",
)

MAX_SNIPPET_RATIO = 0.4


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _packaged_skill() -> str:
    return (SYNAPSE_SKILL / "SKILL.md").read_text(encoding="utf-8")


def _always_on_surfaces() -> dict[str, str]:
    return {
        "global snippet": global_snippet(),
        "project snippet": project_snippet("codex"),
        "server instructions": SERVER_INSTRUCTIONS,
    }


def _assert_describes_skill_contract(text: str, source: str) -> None:
    normalized = _normalized(text)
    for facet, phrases in SKILL_CONTRACT:
        for phrase in phrases:
            assert phrase in normalized, f"{source} does not describe {facet}: missing {phrase!r}"


def test_packaged_skill_describes_the_orchestration_workflow() -> None:
    """The canonical skill is the detailed workflow every managed surface points to."""
    _assert_describes_skill_contract(_packaged_skill(), "packaged SKILL.md")


@pytest.mark.parametrize("agent", ["codex", "claude-code"])
def test_installed_skill_describes_the_orchestration_workflow(
    isolated_home: Path,
    agent: str,
) -> None:
    """What agents actually read on disk carries the same contract."""
    result = install_global_skill(agent)

    installed = (result.path / "SKILL.md").read_text(encoding="utf-8")
    assert MANAGED_SKILL_MARKER in installed
    _assert_describes_skill_contract(installed, f"installed SKILL.md for {agent}")


def test_reinstall_replaces_stale_managed_skill_content(isolated_home: Path) -> None:
    """A normal reinstall upgrades a previous managed skill to the canonical content."""
    target = resolve_global_skill_path("claude-code")
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        f"{MANAGED_SKILL_MARKER}\n\n# Old workflow\n\nCall synapse_search_symbols first.\n",
        encoding="utf-8",
    )

    result = install_global_skill("claude-code")

    assert result.status == "updated"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == _packaged_skill()


def test_always_on_surfaces_share_the_contract_without_restating_it() -> None:
    """Snippets and the handshake stay short pointers, not copies of the skill."""
    skill = _packaged_skill()

    for name, text in _always_on_surfaces().items():
        normalized = _normalized(text)
        for phrase in SNIPPET_CONTRACT:
            assert phrase in normalized, f"{name} is missing {phrase!r}"
        assert len(text) <= len(skill) * MAX_SNIPPET_RATIO, (
            f"{name} is {len(text)} chars against a {len(skill)}-char skill; "
            "always-on surfaces must stay materially shorter"
        )


def test_always_on_surfaces_treat_returned_source_as_read() -> None:
    """Every surface forbids re-running the investigation after a successful inspection."""
    for name, text in _always_on_surfaces().items():
        normalized = _normalized(text)
        assert "Treat returned source as read" in normalized, name
        forbids_rerun = any(
            phrase in normalized
            for phrase in ("broad repository search", "repeat the whole investigation")
        )
        assert forbids_rerun, f"{name} does not forbid re-running the investigation"
