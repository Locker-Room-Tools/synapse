"""Installation and removal of the managed Synapse workflow skill."""

from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from synapse.cli.adapters.model import AgentAdapter, PathSpec, SkillInstallResult
from synapse.cli.adapters.paths import resolve_project_path, resolve_user_path
from synapse.cli.adapters.registry import get_adapter

SKILLS_ROOT = resources.files("synapse") / "skills"
SYNAPSE_SKILL = SKILLS_ROOT / "synapse-code-context"

MANAGED_SKILL_RELATIVE_PATHS: tuple[str, ...] = ("SKILL.md", "agents/openai.yaml")
MANAGED_SKILL_MARKER = "<!-- SYNAPSE MANAGED SKILL -->"


def resolve_global_skill_path(agent_id: str) -> Path:
    """Return the global Synapse skill directory for an adapter."""
    return resolve_user_path(_require_skill(get_adapter(agent_id), "global"))


def resolve_project_skill_path(agent_id: str, workspace_path: str | Path) -> Path:
    """Return the project Synapse skill directory for an adapter."""
    return resolve_project_path(_require_skill(get_adapter(agent_id), "project"), workspace_path)


def _require_skill(adapter: AgentAdapter, scope: str) -> PathSpec:
    spec = adapter.global_skill if scope == "global" else adapter.project_skill
    if spec is None:
        msg = f"{adapter.display_name} does not support {scope}-scope skills."
        raise ValueError(msg)
    return spec


def _skill_files(adapter: AgentAdapter) -> tuple[tuple[str, Traversable], ...]:
    return tuple(
        (relative, SYNAPSE_SKILL.joinpath(*relative.split("/"))) for relative in adapter.skill_files
    )


def _prune_empty_skill_dirs(destination: Path, target: Path) -> None:
    parent = destination.parent
    if parent != target and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()


def install_skill(
    adapter: AgentAdapter,
    target: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> SkillInstallResult:
    """Install or update the managed Synapse skill at a resolved directory."""
    skill_path = target / "SKILL.md"
    desired = {
        relative: source.read_text(encoding="utf-8") for relative, source in _skill_files(adapter)
    }
    stale = [
        relative
        for relative in MANAGED_SKILL_RELATIVE_PATHS
        if relative not in desired and (target / relative).exists()
    ]
    existing_managed = skill_path.exists() and MANAGED_SKILL_MARKER in skill_path.read_text(
        encoding="utf-8"
    )
    target_exists = target.exists()
    unchanged = (
        target_exists
        and not stale
        and all(
            (target / relative).exists()
            and (target / relative).read_text(encoding="utf-8") == content
            for relative, content in desired.items()
        )
    )
    if unchanged:
        return SkillInstallResult(target, "unchanged")
    if target_exists and not existing_managed and not force:
        msg = f"{target} already contains an unmanaged skill; use --force."
        raise FileExistsError(msg)
    if dry_run:
        return SkillInstallResult(target, "would-update" if target_exists else "would-create")
    for relative, content in desired.items():
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    for relative in stale:
        destination = target / relative
        destination.unlink(missing_ok=True)
        _prune_empty_skill_dirs(destination, target)
    return SkillInstallResult(target, "updated" if target_exists else "created")


def remove_skill(target: Path, *, dry_run: bool = False) -> SkillInstallResult:
    """Remove only files owned by the managed Synapse skill."""
    skill_path = target / "SKILL.md"
    if not skill_path.exists():
        return SkillInstallResult(target, "absent")
    if MANAGED_SKILL_MARKER not in skill_path.read_text(encoding="utf-8"):
        return SkillInstallResult(target, "unmanaged")
    if dry_run:
        return SkillInstallResult(target, "would-remove")
    for relative in reversed(MANAGED_SKILL_RELATIVE_PATHS):
        destination = target / relative
        destination.unlink(missing_ok=True)
        _prune_empty_skill_dirs(destination, target)
    if target.exists() and not any(target.iterdir()):
        target.rmdir()
    return SkillInstallResult(target, "removed")


def install_global_skill(
    agent_id: str,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> SkillInstallResult:
    """Install or update the managed Synapse skill for global agent scope."""
    adapter = get_adapter(agent_id)
    target = resolve_user_path(_require_skill(adapter, "global"))
    return install_skill(adapter, target, force=force, dry_run=dry_run)


def remove_global_skill(agent_id: str, *, dry_run: bool = False) -> SkillInstallResult:
    """Remove the managed Synapse skill from global agent scope."""
    adapter = get_adapter(agent_id)
    target = resolve_user_path(_require_skill(adapter, "global"))
    return remove_skill(target, dry_run=dry_run)


def install_project_skill(
    agent_id: str,
    workspace_path: str | Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> SkillInstallResult:
    """Install or update the managed Synapse skill inside a repository."""
    adapter = get_adapter(agent_id)
    target = resolve_project_path(_require_skill(adapter, "project"), workspace_path)
    return install_skill(adapter, target, force=force, dry_run=dry_run)


def remove_project_skill(
    agent_id: str,
    workspace_path: str | Path,
    *,
    dry_run: bool = False,
) -> SkillInstallResult:
    """Remove the managed Synapse skill from a repository."""
    adapter = get_adapter(agent_id)
    target = resolve_project_path(_require_skill(adapter, "project"), workspace_path)
    return remove_skill(target, dry_run=dry_run)
