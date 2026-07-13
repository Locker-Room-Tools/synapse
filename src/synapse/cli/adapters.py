"""Agent adapter helpers for MCP config and instruction snippets."""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from synapse.core.workspace import normalize_workspace_path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ADAPTERS_ROOT = REPOSITORY_ROOT / "adapters"

BEGIN_MARKER = "<!-- BEGIN SYNAPSE CONTEXT ENGINE -->"
END_MARKER = "<!-- END SYNAPSE CONTEXT ENGINE -->"


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
    snippet_path: Path
    default_instruction_file: str
    config: ConfigTarget
    default_scope: str


@dataclass(frozen=True, slots=True)
class InstructionInstallResult:
    """Result of installing an agent instruction snippet."""

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
        default_scope="user",
    ),
    "opencode": AgentAdapter(
        id="opencode",
        display_name="OpenCode",
        snippet_path=ADAPTERS_ROOT / "opencode" / "instructions-snippet.md",
        default_instruction_file="AGENTS.md",
        config=ConfigTarget(
            relative_path="opencode.json",
            user_path="~/.config/opencode/opencode.json",
            fmt="json",
            json_key_path=("mcp",),
        ),
        default_scope="project",
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


def render_mcp_config(
    workspace_path: str | Path,
    *,
    agent_id: str | None = None,
    python_executable: str | None = None,
) -> str:
    """Render a workspace-pinned MCP server config."""
    workspace_root = normalize_workspace_path(workspace_path)
    command = python_executable or sys.executable
    if agent_id == "codex":
        return _render_codex_toml(workspace_root, command)
    if agent_id == "opencode":
        payload = {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {
                "synapse": {
                    "type": "local",
                    "enabled": True,
                    "command": [
                        command,
                        "-m",
                        "synapse",
                        "serve",
                        "--workspace",
                        str(workspace_root),
                    ],
                }
            },
        }
        return json.dumps(payload, indent=2) + "\n"

    payload = {
        "mcpServers": {
            "synapse": {
                "command": command,
                "args": [
                    "-m",
                    "synapse",
                    "serve",
                    "--workspace",
                    str(workspace_root),
                ],
            }
        }
    }
    return json.dumps(payload, indent=2) + "\n"


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _render_codex_toml(workspace_root: Path, command: str) -> str:
    args = ["-m", "synapse", "serve", "--workspace", str(workspace_root)]
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


def _marked_block(snippet: str) -> str:
    return f"{BEGIN_MARKER}\n{snippet.strip()}\n{END_MARKER}"


def _replace_marked_block(existing: str, block: str, *, force: bool) -> tuple[str, str]:
    start = existing.find(BEGIN_MARKER)
    end = existing.find(END_MARKER)
    has_start = start != -1
    has_end = end != -1
    if has_start != has_end:
        msg = "Instruction file contains partial Synapse markers; fix the file manually."
        raise ValueError(msg)
    if not has_start:
        text = existing.rstrip()
        next_text = f"{text}\n\n{block}\n" if text else f"{block}\n"
        return next_text, "updated"

    block_end = end + len(END_MARKER)
    current_block = existing[start:block_end].strip()
    if current_block == block:
        return existing, "unchanged"
    if not force:
        msg = "Synapse instruction block already exists; use --force to replace it."
        raise FileExistsError(msg)

    prefix = existing[:start].rstrip()
    suffix = existing[block_end:].lstrip("\n").rstrip()
    parts = [part for part in (prefix, block, suffix) if part]
    return "\n\n".join(parts) + "\n", "updated"


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


def _remove_marked_block(existing: str) -> tuple[str, bool]:
    start = existing.find(BEGIN_MARKER)
    end = existing.find(END_MARKER)
    has_start = start != -1
    has_end = end != -1
    if has_start != has_end:
        msg = "Instruction file contains partial Synapse markers; fix the file manually."
        raise ValueError(msg)
    if not has_start:
        return existing, False

    block_end = end + len(END_MARKER)
    prefix = existing[:start].rstrip()
    suffix = existing[block_end:].lstrip("\n").rstrip()
    parts = [part for part in (prefix, suffix) if part]
    next_text = "\n\n".join(parts)
    return (f"{next_text}\n" if next_text else ""), True


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
