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
    ("default workflow", ("plan → orient → inspect", "close gaps → synthesize")),
    ("fact planning", ("few concrete facts", "different relevant architectural layers")),
    (
        "bounded orientation",
        (
            "4–8 discriminative terms",
            "don't invent repository symbols",
            "don't path-scope prematurely",
            "refine once",
        ),
    ),
    (
        "diverse production anchors",
        (
            "2–3 diverse production anchors",
            "Prefer production symbols unless tests are relevant",
        ),
    ),
    (
        "relation follow-up",
        ("specific open fact", "Do not inspect the same handle twice"),
    ),
    (
        "bounded gap closing",
        (
            "verified",
            "unresolved",
            "one targeted closing attempt",
            "Then stop investigating that fact",
        ),
    ),
    (
        "fallback conditions",
        (
            "truncated source slice",
            "exact configuration string or local value",
            "generated files or unsupported syntax",
            "dynamic-dispatch gap",
        ),
    ),
    (
        "continuation before shell",
        (
            "pass that token alone to `synapse_inspect` before any shell range read "
            "of the same symbol",
            "use shell only if continuation is unavailable, rejected, or exhausted",
        ),
    ),
    (
        "shell limits",
        (
            "start with broad grep",
            "reproduce the whole investigation",
            "upgrade heuristic relations into proven relations",
        ),
    ),
    (
        "evidence semantics",
        (
            "`exact`/`scoped` = structural evidence",
            "`unique-name` = heuristic",
            "`ambiguous`/`unresolved` = hypothesis",
            "empty relations != proof of absence",
            "references/evidence-semantics.md",
        ),
    ),
    (
        "stop states",
        ("verified evidence", "structural inference", "unresolved evidence"),
    ),
)

SNIPPET_CONTRACT: tuple[str, ...] = (
    "synapse_orient",
    "synapse_inspect",
    "4-8 discriminative",
    "2-3 initial facet-diverse anchors",
    "1-2 returned relation handles",
    "common fast path, not a cap",
    "no budget parameter to raise",
    "never proof of absence",
    "named partial/missing facet",
)


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _packaged_skill() -> str:
    return (SYNAPSE_SKILL / "SKILL.md").read_text(encoding="utf-8")


def _packaged_reference() -> str:
    return (SYNAPSE_SKILL / "references" / "evidence-semantics.md").read_text(encoding="utf-8")


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
    """The canonical skill carries the concise orchestration workflow."""
    _assert_describes_skill_contract(_packaged_skill(), "packaged SKILL.md")


def test_packaged_reference_calibrates_evidence_semantics() -> None:
    """Detailed coverage and call semantics stay available outside the core workflow."""
    reference = _normalized(_packaged_reference())

    for phrase in (
        "index-local syntactic and structural evidence",
        "callers",
        "callees",
        "refs_in",
        "refs_out",
        "coverage.extraction[].call_kinds",
        "payload_complete",
        "not proof of absence",
    ):
        assert phrase in reference


@pytest.mark.parametrize("agent", ["codex", "claude-code"])
def test_installed_skill_describes_the_orchestration_workflow(
    isolated_home: Path,
    agent: str,
) -> None:
    """What agents actually read on disk carries the same contract."""
    result = install_global_skill(agent)

    installed = (result.path / "SKILL.md").read_text(encoding="utf-8")
    installed_reference = (result.path / "references" / "evidence-semantics.md").read_text(
        encoding="utf-8"
    )
    assert MANAGED_SKILL_MARKER in installed
    _assert_describes_skill_contract(installed, f"installed SKILL.md for {agent}")
    assert installed_reference == _packaged_reference()


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


def test_always_on_surfaces_share_the_navigation_contract() -> None:
    """Snippets and the handshake retain the minimum always-on navigation contract."""
    for name, text in _always_on_surfaces().items():
        normalized = _normalized(text)
        for phrase in SNIPPET_CONTRACT:
            assert phrase in normalized, f"{name} is missing {phrase!r}"


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


def test_no_surface_mandates_a_single_inspection_or_exact_call_count() -> None:
    """The bounded lifecycle replaced the one-inspection, two-call ceiling everywhere."""
    surfaces = {**_always_on_surfaces(), "packaged SKILL.md": _packaged_skill()}
    for name, text in surfaces.items():
        normalized = _normalized(text)
        assert "synapse_inspect once" not in normalized, name
        assert "exactly two" not in normalized.lower(), name
