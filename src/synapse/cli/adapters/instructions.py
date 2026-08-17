"""Installation and removal of Synapse-managed agent instructions.

Two ownership modes exist. ``BLOCK`` splices a heading-anchored section into a
file the agent shares with the user (``CLAUDE.md``, ``AGENTS.md``). ``OWNED``
writes a dedicated rule file that Synapse owns end to end. Ownership is proven
by the snippet's own heading line — no HTML-comment markers enter agent
context. Blocks delimited by the pre-0.5.1 BEGIN/END markers are still
recognized and migrated on the next install or removal.
"""

from importlib import resources
from pathlib import Path

from synapse.cli.adapters.model import (
    AgentAdapter,
    InstructionInstallResult,
    InstructionMode,
    InstructionTarget,
)
from synapse.cli.adapters.paths import resolve_project_path, resolve_user_path
from synapse.cli.adapters.registry import get_adapter
from synapse.cli.marker_blocks import (
    append_marker_block,
    find_heading_block,
    find_marker_block,
    splice_marker_block,
)
from synapse.core.workspace import normalize_workspace_path

ADAPTERS_ROOT = resources.files("synapse") / "adapters"
PROJECT_INSTRUCTION_SNIPPET = ADAPTERS_ROOT / "project-instructions-snippet.md"
GLOBAL_INSTRUCTION_SNIPPET = ADAPTERS_ROOT / "global-instructions-snippet.md"

MANAGED_HEADINGS: tuple[str, ...] = (
    "## Synapse Context Engine (use first)",
    "## Synapse code context",
)
LEGACY_BEGIN_MARKER = "<!-- BEGIN SYNAPSE CONTEXT ENGINE -->"
LEGACY_END_MARKER = "<!-- END SYNAPSE CONTEXT ENGINE -->"
_PARTIAL_MARKERS_MESSAGE = (
    "Instruction file contains partial legacy Synapse markers; fix the file manually."
)


def find_managed_instruction_span(existing: str) -> tuple[int, int] | None:
    """Locate the managed instruction block: legacy marker pair first, then heading."""
    legacy = find_marker_block(
        existing, LEGACY_BEGIN_MARKER, LEGACY_END_MARKER, partial_message=_PARTIAL_MARKERS_MESSAGE
    )
    if legacy is not None:
        return legacy
    return find_heading_block(existing, MANAGED_HEADINGS)


def has_managed_instruction_block(existing: str) -> bool:
    """Return whether the text contains a Synapse-managed instruction block."""
    try:
        return find_managed_instruction_span(existing) is not None
    except ValueError:
        return True


def project_snippet(agent_id: str) -> str:
    """Render the shared project instruction snippet for one adapter."""
    template = PROJECT_INSTRUCTION_SNIPPET.read_text(encoding="utf-8")
    return template.format(agent_id=agent_id)


def global_snippet() -> str:
    """Return the agent-independent global bootstrap snippet."""
    return GLOBAL_INSTRUCTION_SNIPPET.read_text(encoding="utf-8")


def _owned_document(block: str, target: InstructionTarget) -> str:
    if not target.frontmatter:
        return f"{block}\n"
    lines = [f"{key}: {value}" for key, value in target.frontmatter]
    return "---\n" + "\n".join(lines) + "\n---\n\n" + block + "\n"


def _target_path(
    workspace_root: Path,
    relative_path: str,
    output_path: str | Path | None,
) -> Path:
    if output_path is None:
        return workspace_root / relative_path
    candidate = Path(output_path).expanduser()
    if candidate.is_absolute():
        return candidate
    return workspace_root / candidate


def resolve_instruction_path(
    agent_id: str,
    workspace_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> Path:
    """Resolve the repository instruction target for an adapter."""
    adapter = get_adapter(agent_id)
    workspace_root = normalize_workspace_path(workspace_path)
    target = adapter.project_instructions
    if target is None:
        msg = f"{adapter.display_name} does not support project instructions."
        raise ValueError(msg)
    return _target_path(workspace_root, target.location.path, output_path)


def resolve_global_instruction_path(agent_id: str) -> Path:
    """Return the global instruction file for an adapter."""
    adapter = get_adapter(agent_id)
    target = adapter.global_instructions
    if target is None:
        msg = f"{adapter.display_name} does not support global instructions."
        raise ValueError(msg)
    return resolve_user_path(target.location)


def _replace_managed_block(existing: str, block: str) -> tuple[str, str]:
    """Replace a Synapse-owned block in place.

    A span found here is provably Synapse-managed, so replacing it needs no
    ``--force``: several adapters share one instruction file (``AGENTS.md``),
    and installing a second of them must update the single block rather than
    fail. Unmanaged content is never touched — it yields no span and the block
    is appended instead.
    """
    span = find_managed_instruction_span(existing)
    if span is None:
        return append_marker_block(existing, block), "updated"

    start, block_end = span
    if existing[start:block_end].strip() == block:
        return existing, "unchanged"
    return splice_marker_block(existing, span, block), "updated"


def install_instructions(
    path: Path,
    snippet: str,
    target: InstructionTarget,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> InstructionInstallResult:
    """Install a Synapse instruction snippet at a resolved path."""
    block = snippet.strip()
    if target.mode is InstructionMode.OWNED:
        return _install_owned(path, block, target, force=force, dry_run=dry_run)
    return _install_block(path, block, dry_run=dry_run)


def remove_instructions(
    path: Path,
    target: InstructionTarget,
    *,
    dry_run: bool = False,
) -> InstructionInstallResult:
    """Remove Synapse-managed instruction content at a resolved path."""
    if target.mode is InstructionMode.OWNED:
        return _remove_owned(path, dry_run=dry_run)
    return _remove_block(path, dry_run=dry_run)


def _install_block(
    path: Path,
    block: str,
    *,
    dry_run: bool,
) -> InstructionInstallResult:
    if not path.exists():
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{block}\n", encoding="utf-8")
        return InstructionInstallResult(path, "would-create" if dry_run else "created")

    existing = path.read_text(encoding="utf-8")
    next_text, status = _replace_managed_block(existing, block)
    if status == "unchanged":
        return InstructionInstallResult(path, status)
    if not dry_run:
        path.write_text(next_text, encoding="utf-8")
    return InstructionInstallResult(path, "would-update" if dry_run else status)


def _install_owned(
    path: Path,
    block: str,
    target: InstructionTarget,
    *,
    force: bool,
    dry_run: bool,
) -> InstructionInstallResult:
    document = _owned_document(block, target)
    if not path.exists():
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(document, encoding="utf-8")
        return InstructionInstallResult(path, "would-create" if dry_run else "created")

    existing = path.read_text(encoding="utf-8")
    if existing == document:
        return InstructionInstallResult(path, "unchanged")
    if not force:
        if not has_managed_instruction_block(existing):
            msg = f"{path} already contains an unmanaged instruction file; use --force."
        else:
            msg = "Synapse instruction block already exists; use --force to replace it."
        raise FileExistsError(msg)
    if not dry_run:
        path.write_text(document, encoding="utf-8")
    return InstructionInstallResult(path, "would-update" if dry_run else "updated")


def _remove_block(path: Path, *, dry_run: bool) -> InstructionInstallResult:
    if not path.exists():
        return InstructionInstallResult(path, "absent")
    existing = path.read_text(encoding="utf-8")
    span = find_managed_instruction_span(existing)
    if span is None:
        return InstructionInstallResult(path, "absent")
    if dry_run:
        return InstructionInstallResult(path, "would-remove")
    next_text = splice_marker_block(existing, span)
    if next_text:
        path.write_text(next_text, encoding="utf-8")
    else:
        path.unlink()
    return InstructionInstallResult(path, "removed")


def _remove_owned(path: Path, *, dry_run: bool) -> InstructionInstallResult:
    if not path.exists():
        return InstructionInstallResult(path, "absent")
    if not has_managed_instruction_block(path.read_text(encoding="utf-8")):
        return InstructionInstallResult(path, "unmanaged")
    if dry_run:
        return InstructionInstallResult(path, "would-remove")
    path.unlink()
    _prune_empty_parents(path)
    return InstructionInstallResult(path, "removed")


def _prune_empty_parents(path: Path) -> None:
    parent = path.parent
    if parent.exists() and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()


def install_instruction_snippet(
    agent_id: str,
    workspace_path: str | Path,
    *,
    output_path: str | Path | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> InstructionInstallResult:
    """Install an adapter instruction snippet into a repository file."""
    target = _require_project_target(get_adapter(agent_id))
    path = resolve_instruction_path(agent_id, workspace_path, output_path=output_path)
    return install_instructions(
        path,
        project_snippet(agent_id),
        target,
        force=force,
        dry_run=dry_run,
    )


def remove_instruction_snippet(
    agent_id: str,
    workspace_path: str | Path,
    *,
    output_path: str | Path | None = None,
    dry_run: bool = False,
) -> InstructionInstallResult:
    """Remove a Synapse instruction snippet from a repository file."""
    target = _require_project_target(get_adapter(agent_id))
    path = resolve_instruction_path(agent_id, workspace_path, output_path=output_path)
    return remove_instructions(path, target, dry_run=dry_run)


def install_global_instruction(
    agent_id: str,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> InstructionInstallResult:
    """Install the concise Synapse bootstrap rule in global agent instructions."""
    target = _require_global_target(get_adapter(agent_id))
    path = resolve_global_instruction_path(agent_id)
    return install_instructions(path, global_snippet(), target, force=force, dry_run=dry_run)


def remove_global_instruction(
    agent_id: str,
    *,
    dry_run: bool = False,
) -> InstructionInstallResult:
    """Remove the marker-managed global Synapse bootstrap rule."""
    target = _require_global_target(get_adapter(agent_id))
    path = resolve_global_instruction_path(agent_id)
    return remove_instructions(path, target, dry_run=dry_run)


def _require_project_target(adapter: AgentAdapter) -> InstructionTarget:
    if adapter.project_instructions is None:
        msg = f"{adapter.display_name} does not support project instructions."
        raise ValueError(msg)
    return adapter.project_instructions


def _require_global_target(adapter: AgentAdapter) -> InstructionTarget:
    if adapter.global_instructions is None:
        msg = f"{adapter.display_name} does not support global instructions."
        raise ValueError(msg)
    return adapter.global_instructions


def project_instruction_path(adapter: AgentAdapter, workspace_path: str | Path) -> Path:
    """Resolve an adapter's default project instruction path."""
    target = _require_project_target(adapter)
    return resolve_project_path(target.location, workspace_path)
