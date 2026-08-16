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
from synapse.cli.adapters.skills import (
    LEGACY_MANAGED_SKILL_MARKER,
    MANAGED_SKILL_MANIFEST,
    SYNAPSE_SKILL,
)
from synapse.core.navigation import estimate_tokens
from synapse.mcp.instructions import SERVER_INSTRUCTIONS

SKILL_CONTRACT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("navigation tools", ("synapse_orient", "synapse_inspect")),
    ("default workflow", ("plan → orient → inspect", "close gaps → synthesize")),
    (
        "clause-to-facet planning",
        (
            "each material clause",
            "requested deliverables",
            "Merge overlapping facets",
            "typically 3–7",
            "different relevant architectural layers",
        ),
    ),
    (
        "facet ledger",
        (
            "verified, partial, or missing",
            "best evidence (file:line)",
            "stays closed",
            "final-answer planning",
        ),
    ),
    (
        "bounded orientation",
        (
            "4–8 discriminative terms",
            "don't invent repository symbols",
            "don't path-scope prematurely",
            "refine once",
            "path fragments from files already seen",
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
        ("specific open facet", "Do not inspect the same handle twice"),
    ),
    (
        "bounded gap closing",
        (
            "verified",
            "unresolved",
            "one targeted closing attempt",
            "continuation token",
            "Then stop investigating that facet",
        ),
    ),
    (
        "fallback conditions",
        (
            "truncated source slice",
            "no continuation token was offered",
            "exact configuration string or local value",
            "generated files or unsupported syntax",
            "dynamic-dispatch gap",
        ),
    ),
    (
        "shell limits",
        (
            "start with broad grep",
            "reproduce the whole investigation",
            "reread ranges Synapse already returned",
            "upgrade heuristic relations into proven relations",
        ),
    ),
    (
        "reliability claims",
        (
            "verified failure path",
            "unresolved hypothesis",
            "Both are valid deliverables",
            "initiating state or fault",
            "code path that permits it",
            "fails to eliminate it",
            "observable outcome",
            "completing the chain by assumption",
        ),
    ),
    (
        "ledger-driven synthesis",
        (
            "from the ledger, not from the most recent payloads",
            "account for every facet",
            "explicitly unresolved",
            "execution order",
            "evidence strength actually held",
            "not a reason for more tool calls",
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
    "requested deliverables",
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
    assert "<!--" not in installed
    assert "managed-by" not in installed
    assert (result.path / MANAGED_SKILL_MANIFEST).is_file()
    _assert_describes_skill_contract(installed, f"installed SKILL.md for {agent}")
    assert installed_reference == _packaged_reference()


def test_reinstall_migrates_a_legacy_marker_managed_skill(isolated_home: Path) -> None:
    """A pre-0.5.1 skill stamped with the HTML marker upgrades without --force."""
    target = resolve_global_skill_path("claude-code")
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        f"{LEGACY_MANAGED_SKILL_MARKER}\n\n# Old workflow\n\nCall synapse_search_symbols first.\n",
        encoding="utf-8",
    )

    result = install_global_skill("claude-code")

    assert result.status == "updated"
    installed = (target / "SKILL.md").read_text(encoding="utf-8")
    assert installed == _packaged_skill()
    assert LEGACY_MANAGED_SKILL_MARKER not in installed
    assert "managed-by" not in installed
    assert (target / MANAGED_SKILL_MANIFEST).is_file()


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


def test_detailed_surfaces_share_the_three_state_ledger() -> None:
    """Skill, handshake, and project snippet name the same three facet states."""
    surfaces = {
        "packaged SKILL.md": _packaged_skill(),
        "server instructions": SERVER_INSTRUCTIONS,
        "project snippet": project_snippet("codex"),
    }
    for name, text in surfaces.items():
        assert "verified, partial, or missing" in _normalized(text), name


def test_agent_visible_managed_content_carries_no_html_comments() -> None:
    """No HTML-comment markup enters agent context through managed content."""
    surfaces = {
        **_always_on_surfaces(),
        "packaged SKILL.md": _packaged_skill(),
        "packaged reference": _packaged_reference(),
    }
    for name, text in surfaces.items():
        assert "<!--" not in text, name
        assert "managed-by" not in text, name


def test_static_instruction_surfaces_stay_within_token_ceilings() -> None:
    """Instruction surfaces cannot silently grow into duplicated checklists."""
    ceilings = {
        "packaged SKILL.md": (_packaged_skill(), 1100),
        "packaged reference": (_packaged_reference(), 600),
        "server instructions": (SERVER_INSTRUCTIONS, 750),
        "global snippet": (global_snippet(), 500),
        "project snippet": (project_snippet("codex"), 700),
    }
    for name, (text, ceiling) in ceilings.items():
        estimated = estimate_tokens(text)
        assert estimated <= ceiling, f"{name} is {estimated} estimated tokens (ceiling {ceiling})"
