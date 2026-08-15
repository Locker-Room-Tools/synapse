"""Installation and removal of the managed Synapse workflow skill."""

import hashlib
import json
import re
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from synapse.cli.adapters.model import AgentAdapter, PathSpec, SkillInstallResult
from synapse.cli.adapters.paths import resolve_project_path, resolve_user_path
from synapse.cli.adapters.registry import get_adapter

SKILLS_ROOT = resources.files("synapse") / "skills"
SYNAPSE_SKILL = SKILLS_ROOT / "synapse-code-context"

MANAGED_SKILL_RELATIVE_PATHS: tuple[str, ...] = (
    "SKILL.md",
    "agents/openai.yaml",
    "references/evidence-semantics.md",
)
MANAGED_SKILL_MANIFEST = ".synapse-managed.json"
MANAGED_SKILL_MANIFEST_SCHEMA_VERSION = 1
LEGACY_MANAGED_SKILL_MARKER = "<!-- SYNAPSE MANAGED SKILL -->"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class _ManagedSkillManifest:
    files: dict[str, str]


def _content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _manifest_path(target: Path) -> Path:
    return target / MANAGED_SKILL_MANIFEST


def _render_manifest(files: dict[str, str]) -> str:
    payload = {
        "files": dict(sorted(files.items())),
        "managed_by": "synapse",
        "schema_version": MANAGED_SKILL_MANIFEST_SCHEMA_VERSION,
    }
    return f"{json.dumps(payload, indent=2, sort_keys=True)}\n"


def _read_manifest(target: Path) -> _ManagedSkillManifest | None:
    path = _manifest_path(target)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if set(payload) != {"files", "managed_by", "schema_version"}:
        return None
    if payload.get("managed_by") != "synapse":
        return None
    if payload.get("schema_version") != MANAGED_SKILL_MANIFEST_SCHEMA_VERSION:
        return None
    raw_files = payload.get("files")
    if not isinstance(raw_files, dict) or "SKILL.md" not in raw_files:
        return None
    files: dict[str, str] = {}
    for relative, digest in raw_files.items():
        if not isinstance(relative, str) or relative not in MANAGED_SKILL_RELATIVE_PATHS:
            return None
        if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
            return None
        files[relative] = digest
    return _ManagedSkillManifest(files)


def _files_match_manifest(target: Path, manifest: _ManagedSkillManifest) -> bool:
    for relative, expected in manifest.files.items():
        destination = target / relative
        if not destination.is_file() or destination.is_symlink():
            return False
        try:
            actual = _content_sha256(destination.read_bytes())
        except OSError:
            return False
        if actual != expected:
            return False
    return True


def _has_legacy_marker(target: Path) -> bool:
    skill_path = target / "SKILL.md"
    if not skill_path.is_file() or skill_path.is_symlink():
        return False
    try:
        return LEGACY_MANAGED_SKILL_MARKER in skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False


def _desired_skill_files(adapter: AgentAdapter) -> dict[str, bytes]:
    return {
        relative: source.read_text(encoding="utf-8").encode("utf-8")
        for relative, source in _skill_files(adapter)
    }


def _desired_hashes(desired: dict[str, bytes]) -> dict[str, str]:
    return {relative: _content_sha256(content) for relative, content in desired.items()}


def _validate_skill_destinations(target: Path, relatives: tuple[str, ...]) -> None:
    for relative in relatives:
        parent = target
        for part in Path(relative).parts[:-1]:
            parent /= part
            if parent.is_symlink():
                msg = f"Managed skill path crosses a symbolic-link directory: {parent}"
                raise FileExistsError(msg)
        destination = target / relative
        if destination.exists() and destination.is_dir():
            msg = f"Managed skill file path is a directory: {destination}"
            raise FileExistsError(msg)


def _write_skill_file(target: Path, relative: str, content: bytes) -> None:
    destination = target / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        destination.unlink()
    destination.write_bytes(content)


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
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        msg = f"{target} is not a writable skill directory."
        raise FileExistsError(msg)
    desired = _desired_skill_files(adapter)
    desired_hashes = _desired_hashes(desired)
    target_exists = target.exists()
    manifest_file = _manifest_path(target)
    manifest_exists = manifest_file.exists() or manifest_file.is_symlink()
    manifest = _read_manifest(target) if manifest_exists else None
    legacy_managed = not manifest_exists and _has_legacy_marker(target)

    if manifest is not None:
        files_unchanged = _files_match_manifest(target, manifest)
        if not files_unchanged and not force:
            msg = f"{target} contains modified managed skill files; use --force."
            raise FileExistsError(msg)
        new_path_collisions = [
            relative
            for relative in set(desired) - set(manifest.files)
            if (target / relative).exists() or (target / relative).is_symlink()
        ]
        if new_path_collisions and not force:
            joined = ", ".join(sorted(new_path_collisions))
            msg = f"{target} contains untracked files at new managed paths: {joined}; use --force."
            raise FileExistsError(msg)
        if files_unchanged and manifest.files == desired_hashes:
            return SkillInstallResult(target, "unchanged")
    elif target_exists and not legacy_managed and not force:
        msg = f"{target} already contains an unmanaged skill; use --force."
        raise FileExistsError(msg)

    if dry_run:
        return SkillInstallResult(target, "would-update" if target_exists else "would-create")

    _validate_skill_destinations(target, tuple(desired))
    if manifest is not None:
        stale = set(manifest.files) - set(desired)
    elif legacy_managed:
        stale = set(MANAGED_SKILL_RELATIVE_PATHS) - set(desired)
    else:
        stale = set()
    for relative in sorted(stale, reverse=True):
        destination = target / relative
        destination.unlink(missing_ok=True)
        _prune_empty_skill_dirs(destination, target)

    for relative, content in desired.items():
        _write_skill_file(target, relative, content)
    target.mkdir(parents=True, exist_ok=True)
    if manifest_file.is_symlink():
        manifest_file.unlink()
    manifest_file.write_text(_render_manifest(desired_hashes), encoding="utf-8")
    return SkillInstallResult(target, "updated" if target_exists else "created")


def remove_skill(target: Path, *, dry_run: bool = False) -> SkillInstallResult:
    """Remove only files owned by the managed Synapse skill."""
    if target.is_symlink():
        return SkillInstallResult(target, "unmanaged")
    if not target.exists():
        return SkillInstallResult(target, "absent")
    if not target.is_dir():
        return SkillInstallResult(target, "unmanaged")

    manifest_file = _manifest_path(target)
    manifest_exists = manifest_file.exists() or manifest_file.is_symlink()
    manifest = _read_manifest(target) if manifest_exists else None
    legacy_managed = not manifest_exists and _has_legacy_marker(target)
    if manifest_exists and manifest is None:
        return SkillInstallResult(target, "unmanaged")
    if manifest is not None and not _files_match_manifest(target, manifest):
        return SkillInstallResult(target, "modified")
    if manifest is None and not legacy_managed:
        return SkillInstallResult(target, "unmanaged")
    if dry_run:
        return SkillInstallResult(target, "would-remove")

    managed_paths = manifest.files if manifest is not None else MANAGED_SKILL_RELATIVE_PATHS
    for relative in sorted(managed_paths, reverse=True):
        destination = target / relative
        destination.unlink(missing_ok=True)
        _prune_empty_skill_dirs(destination, target)
    manifest_file.unlink(missing_ok=True)
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
