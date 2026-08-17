"""The contributor skills ship in three catalogs that must stay byte-identical.

`.claude/skills/` is the canonical copy; `.agents/skills/` (the cross-agent
Agent Skills convention) and `.github/skills/` (GitHub Copilot) are mirrors.
Only the contributor skills listed below are compared — the same catalogs may
also hold the managed `synapse-code-context` skill that `synapse install`
writes. Edit the canonical copy and re-copy; this test fails on any drift.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL = REPO_ROOT / ".claude" / "skills"
MIRRORS = (REPO_ROOT / ".agents" / "skills", REPO_ROOT / ".github" / "skills")
CONTRIBUTOR_SKILLS = (
    "ab-eval",
    "add-adapter",
    "add-language",
    "add-mcp-tool",
    "release",
    "schema-change",
)


def _skill_files(root: Path, skill: str) -> dict[str, bytes]:
    skill_root = root / skill
    return {
        str(path.relative_to(skill_root)): path.read_bytes()
        for path in sorted(skill_root.rglob("*"))
        if path.is_file()
    }


def test_contributor_skill_catalogs_are_identical() -> None:
    for skill in CONTRIBUTOR_SKILLS:
        canonical = _skill_files(CANONICAL, skill)
        assert canonical, f"canonical .claude/skills/{skill} is missing or empty"
        for mirror in MIRRORS:
            assert _skill_files(mirror, skill) == canonical, (
                f"{mirror.relative_to(REPO_ROOT)}/{skill} has drifted from "
                ".claude/skills; edit .claude/skills and re-copy"
            )
