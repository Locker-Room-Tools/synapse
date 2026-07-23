"""Agent adapter helpers for MCP config and instruction snippets."""

import json
import os
import sys
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from synapse.cli.marker_blocks import append_marker_block, find_marker_block, splice_marker_block
from synapse.core.workspace import normalize_workspace_path

ADAPTERS_ROOT = resources.files("synapse") / "adapters"
SKILLS_ROOT = resources.files("synapse") / "skills"
GLOBAL_INSTRUCTION_SNIPPET = ADAPTERS_ROOT / "global-instructions-snippet.md"
SYNAPSE_SKILL = SKILLS_ROOT / "synapse-code-context"

MANAGED_SKILL_RELATIVE_PATHS: tuple[str, ...] = ("SKILL.md", "agents/openai.yaml")

BEGIN_MARKER = "<!-- BEGIN SYNAPSE CONTEXT ENGINE -->"
END_MARKER = "<!-- END SYNAPSE CONTEXT ENGINE -->"
_PARTIAL_MARKERS_MESSAGE = (
    "Instruction file contains partial Synapse markers; fix the file manually."
)


@dataclass(frozen=True, slots=True)
class ConfigTarget:
    """MCP config target metadata for one adapter."""

    relative_path: str | None
    user_path: str | None
    fmt: str
    json_key_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentAdapter:
    """Static metadata for one supported agent adapter."""

    id: str
    display_name: str
    snippet_path: Traversable
    default_instruction_file: str
    config: ConfigTarget
    default_scope: str
    global_instruction_path: str
    global_skill_path: str
    skill_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InstructionInstallResult:
    """Result of installing an agent instruction snippet."""

    path: Path
    status: str


@dataclass(frozen=True, slots=True)
class SkillInstallResult:
    """Result of installing or removing the managed Synapse skill."""

    path: Path
    status: str


ADAPTERS: dict[str, AgentAdapter] = {
    "claude-code": AgentAdapter(
        id="claude-code",
        display_name="Claude Code",
        snippet_path=ADAPTERS_ROOT / "claude-code" / "CLAUDE.md-snippet.md",
        default_instruction_file="CLAUDE.md",
        config=ConfigTarget(
            relative_path=".mcp.json",
            user_path="~/.claude.json",
            fmt="json",
            json_key_path=("mcpServers",),
        ),
        default_scope="project",
        global_instruction_path="~/.claude/CLAUDE.md",
        global_skill_path="~/.claude/skills/synapse-code-context",
        skill_files=("SKILL.md",),
    ),
    "codex": AgentAdapter(
        id="codex",
        display_name="Codex",
        snippet_path=ADAPTERS_ROOT / "codex" / "AGENTS.md-snippet.md",
        default_instruction_file="AGENTS.md",
        config=ConfigTarget(
            relative_path=".codex/config.toml",
            user_path="~/.codex/config.toml",
            fmt="toml",
            json_key_path=(),
        ),
        default_scope="project",
        global_instruction_path="~/.codex/AGENTS.md",
        global_skill_path="~/.codex/skills/synapse-code-context",
        skill_files=("SKILL.md", "agents/openai.yaml"),
    ),
    "opencode": AgentAdapter(
        id="opencode",
        display_name="OpenCode",
        snippet_path=ADAPTERS_ROOT / "opencode" / "AGENTS.md-snippet.md",
        default_instruction_file="AGENTS.md",
        config=ConfigTarget(
            relative_path="opencode.json",
            user_path="~/.config/opencode/opencode.json",
            fmt="json",
            json_key_path=("mcp",),
        ),
        default_scope="project",
        global_instruction_path="~/.config/opencode/AGENTS.md",
        global_skill_path="~/.config/opencode/skills/synapse-code-context",
        skill_files=("SKILL.md",),
    ),
}


def adapter_choices() -> tuple[str, ...]:
    """Return supported adapter ids for argparse choices."""
    return tuple(sorted(ADAPTERS))


def get_adapter(agent_id: str) -> AgentAdapter:
    """Return adapter metadata for an agent id."""
    try:
        return ADAPTERS[agent_id]
    except KeyError as exc:
        msg = f"Unsupported agent: {agent_id}"
        raise ValueError(msg) from exc


def resolve_agent_user_path(agent_id: str, configured_path: str) -> Path:
    """Resolve an adapter user path with supported config-home overrides."""
    if agent_id == "codex" and (codex_home := os.environ.get("CODEX_HOME")):
        suffix = Path(configured_path.removeprefix("~/.codex/"))
        return Path(codex_home).expanduser().resolve() / suffix
    if agent_id == "opencode" and (xdg_home := os.environ.get("XDG_CONFIG_HOME")):
        suffix = Path(configured_path.removeprefix("~/.config/"))
        return Path(xdg_home).expanduser().resolve() / suffix
    return Path(configured_path).expanduser()


def render_mcp_config(
    workspace_path: str | Path | None,
    *,
    agent_id: str | None = None,
    python_executable: str | None = None,
) -> str:
    """Render a workspace-pinned or portable global MCP server config."""
    portable = workspace_path is None
    workspace_root = None
    if workspace_path is not None:
        workspace_root = normalize_workspace_path(workspace_path)
    command = "synapse" if portable else (python_executable or sys.executable)
    args = (
        ["serve"]
        if portable
        else [
            "-m",
            "synapse",
            "serve",
            "--workspace",
            str(workspace_root),
        ]
    )
    if agent_id == "codex":
        return _render_codex_toml(command, args)
    if agent_id == "opencode":
        payload = {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {
                "synapse": {
                    "type": "local",
                    "enabled": True,
                    "command": [
                        command,
                        *args,
                    ],
                }
            },
        }
        return json.dumps(payload, indent=2) + "\n"

    payload = {
        "mcpServers": {
            "synapse": {
                "command": command,
                "args": args,
            }
        }
    }
    return json.dumps(payload, indent=2) + "\n"


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _render_codex_toml(command: str, args: list[str]) -> str:
    return f"[mcp_servers.synapse]\ncommand = {_toml_string(command)}\nargs = {_toml_array(args)}\n"


def _target_path(
    workspace_root: Path,
    adapter: AgentAdapter,
    output_path: str | Path | None,
) -> Path:
    if output_path is None:
        return workspace_root / adapter.default_instruction_file
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
    return _target_path(workspace_root, adapter, output_path)


def resolve_global_instruction_path(agent_id: str) -> Path:
    """Return the global instruction file for an adapter."""
    adapter = get_adapter(agent_id)
    return resolve_agent_user_path(agent_id, adapter.global_instruction_path)


def resolve_global_skill_path(agent_id: str) -> Path:
    """Return the global Synapse skill directory for an adapter."""
    adapter = get_adapter(agent_id)
    return resolve_agent_user_path(agent_id, adapter.global_skill_path)


def _marked_block(snippet: str) -> str:
    return f"{BEGIN_MARKER}\n{snippet.strip()}\n{END_MARKER}"


def _replace_marked_block(existing: str, block: str, *, force: bool) -> tuple[str, str]:
    span = find_marker_block(
        existing, BEGIN_MARKER, END_MARKER, partial_message=_PARTIAL_MARKERS_MESSAGE
    )
    if span is None:
        return append_marker_block(existing, block), "updated"

    start, block_end = span
    if existing[start:block_end].strip() == block:
        return existing, "unchanged"
    if not force:
        msg = "Synapse instruction block already exists; use --force to replace it."
        raise FileExistsError(msg)
    return splice_marker_block(existing, span, block), "updated"


def install_instruction_snippet(
    agent_id: str,
    workspace_path: str | Path,
    *,
    output_path: str | Path | None = None,
    force: bool = False,
) -> InstructionInstallResult:
    """Install an adapter instruction snippet into a repository file."""
    adapter = get_adapter(agent_id)
    workspace_root = normalize_workspace_path(workspace_path)
    target = _target_path(workspace_root, adapter, output_path)
    snippet = adapter.snippet_path.read_text(encoding="utf-8")
    block = _marked_block(snippet)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{block}\n", encoding="utf-8")
        return InstructionInstallResult(path=target, status="created")

    existing = target.read_text(encoding="utf-8")
    next_text, status = _replace_marked_block(existing, block, force=force)
    if status != "unchanged":
        target.write_text(next_text, encoding="utf-8")
    return InstructionInstallResult(path=target, status=status)


def install_global_instruction(
    agent_id: str,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> InstructionInstallResult:
    """Install the concise Synapse bootstrap rule in global agent instructions."""
    target = resolve_global_instruction_path(agent_id)
    snippet = GLOBAL_INSTRUCTION_SNIPPET.read_text(encoding="utf-8")
    block = _marked_block(snippet)
    if not target.exists():
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{block}\n", encoding="utf-8")
        return InstructionInstallResult(
            path=target,
            status="would-create" if dry_run else "created",
        )

    existing = target.read_text(encoding="utf-8")
    next_text, status = _replace_marked_block(existing, block, force=force)
    if status == "unchanged":
        return InstructionInstallResult(path=target, status=status)
    if not dry_run:
        target.write_text(next_text, encoding="utf-8")
    return InstructionInstallResult(
        path=target,
        status="would-update" if dry_run else status,
    )


def _remove_marked_block(existing: str) -> tuple[str, bool]:
    span = find_marker_block(
        existing, BEGIN_MARKER, END_MARKER, partial_message=_PARTIAL_MARKERS_MESSAGE
    )
    if span is None:
        return existing, False
    return splice_marker_block(existing, span), True


def remove_instruction_snippet(
    agent_id: str,
    workspace_path: str | Path,
    *,
    output_path: str | Path | None = None,
    dry_run: bool = False,
) -> InstructionInstallResult:
    """Remove a marker-wrapped Synapse instruction snippet from a repository file."""
    target = resolve_instruction_path(agent_id, workspace_path, output_path=output_path)
    if not target.exists():
        return InstructionInstallResult(path=target, status="absent")

    existing = target.read_text(encoding="utf-8")
    next_text, removed = _remove_marked_block(existing)
    if not removed:
        return InstructionInstallResult(path=target, status="absent")
    if dry_run:
        return InstructionInstallResult(path=target, status="would-remove")
    if next_text:
        target.write_text(next_text, encoding="utf-8")
    else:
        target.unlink()
    return InstructionInstallResult(path=target, status="removed")


def remove_global_instruction(
    agent_id: str,
    *,
    dry_run: bool = False,
) -> InstructionInstallResult:
    """Remove the marker-managed global Synapse bootstrap rule."""
    target = resolve_global_instruction_path(agent_id)
    if not target.exists():
        return InstructionInstallResult(path=target, status="absent")
    existing = target.read_text(encoding="utf-8")
    next_text, removed = _remove_marked_block(existing)
    if not removed:
        return InstructionInstallResult(path=target, status="absent")
    if dry_run:
        return InstructionInstallResult(path=target, status="would-remove")
    if next_text:
        target.write_text(next_text, encoding="utf-8")
    else:
        target.unlink()
    return InstructionInstallResult(path=target, status="removed")


def _skill_files(adapter: AgentAdapter) -> tuple[tuple[str, Traversable], ...]:
    return tuple(
        (relative, SYNAPSE_SKILL.joinpath(*relative.split("/"))) for relative in adapter.skill_files
    )


def _prune_empty_skill_dirs(destination: Path, target: Path) -> None:
    parent = destination.parent
    if parent != target and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()


def install_global_skill(
    agent_id: str,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> SkillInstallResult:
    """Install or update the managed Synapse workflow skill."""
    adapter = get_adapter(agent_id)
    target = resolve_global_skill_path(agent_id)
    skill_path = target / "SKILL.md"
    desired = {
        relative: source.read_text(encoding="utf-8") for relative, source in _skill_files(adapter)
    }
    stale = [
        relative
        for relative in MANAGED_SKILL_RELATIVE_PATHS
        if relative not in desired and (target / relative).exists()
    ]
    existing_managed = (
        skill_path.exists()
        and "<!-- SYNAPSE MANAGED SKILL -->" in skill_path.read_text(encoding="utf-8")
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
        return SkillInstallResult(path=target, status="unchanged")
    if target_exists and not existing_managed and not force:
        msg = f"{target} already contains an unmanaged skill; use --force."
        raise FileExistsError(msg)
    if dry_run:
        return SkillInstallResult(
            path=target,
            status="would-update" if target_exists else "would-create",
        )
    for relative, content in desired.items():
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    for relative in stale:
        destination = target / relative
        destination.unlink(missing_ok=True)
        _prune_empty_skill_dirs(destination, target)
    return SkillInstallResult(
        path=target,
        status="updated" if target_exists else "created",
    )


def remove_global_skill(
    agent_id: str,
    *,
    dry_run: bool = False,
) -> SkillInstallResult:
    """Remove only files owned by the managed Synapse skill."""
    target = resolve_global_skill_path(agent_id)
    skill_path = target / "SKILL.md"
    if not skill_path.exists():
        return SkillInstallResult(path=target, status="absent")
    if "<!-- SYNAPSE MANAGED SKILL -->" not in skill_path.read_text(encoding="utf-8"):
        return SkillInstallResult(path=target, status="unmanaged")
    if dry_run:
        return SkillInstallResult(path=target, status="would-remove")
    for relative in reversed(MANAGED_SKILL_RELATIVE_PATHS):
        destination = target / relative
        destination.unlink(missing_ok=True)
        _prune_empty_skill_dirs(destination, target)
    if target.exists() and not any(target.iterdir()):
        target.rmdir()
    return SkillInstallResult(path=target, status="removed")
