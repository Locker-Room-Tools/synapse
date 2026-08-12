"""Declarative adapter model: agent capabilities expressed as data."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]
JsonValue = Any

ConfigScalar = str | bool | tuple[str, ...]


class ConfigFormat(StrEnum):
    """Serialization format of an agent MCP config file."""

    JSON = "json"
    TOML = "toml"
    YAML = "yaml"


class ContainerShape(StrEnum):
    """How an agent stores named MCP servers inside its config."""

    MAPPING = "mapping"
    LIST = "list"


class PayloadStyle(StrEnum):
    """How an agent spells the stdio launch command."""

    COMMAND_ARGS = "command_args"
    COMMAND_LIST = "command_list"


class InstructionMode(StrEnum):
    """How Synapse owns an agent instruction file."""

    BLOCK = "block"
    OWNED = "owned"


@dataclass(frozen=True, slots=True)
class PathSpec:
    """A configured path with an optional environment home override.

    ``path`` is workspace-relative for project scope and ``~``-prefixed for user
    scope. When ``env_var`` is set and present in the environment, its value
    replaces ``env_prefix`` at the front of ``path``.
    """

    path: str
    env_var: str | None = None
    env_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class McpTarget:
    """MCP config contract for one adapter."""

    fmt: ConfigFormat
    shape: ContainerShape
    key_path: tuple[str, ...]
    payload_style: PayloadStyle = PayloadStyle.COMMAND_ARGS
    name_field: str | None = None
    extra_fields: tuple[tuple[str, ConfigScalar], ...] = ()
    document_defaults: tuple[tuple[str, str], ...] = ()
    project: str | None = None
    user: PathSpec | None = None
    user_requires_existing: bool = False


@dataclass(frozen=True, slots=True)
class InstructionTarget:
    """Instruction file contract for one adapter and scope."""

    location: PathSpec
    mode: InstructionMode = InstructionMode.BLOCK
    frontmatter: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class AgentAdapter:
    """Static metadata for one supported agent adapter."""

    id: str
    display_name: str
    mcp: McpTarget
    default_scope: str = "project"
    project_instructions: InstructionTarget | None = None
    global_instructions: InstructionTarget | None = None
    project_skill: PathSpec | None = None
    global_skill: PathSpec | None = None
    skill_files: tuple[str, ...] = (
        "SKILL.md",
        "references/evidence-semantics.md",
    )
    supports_hook: bool = False
    warn_legacy_user_config: bool = False


@dataclass(frozen=True, slots=True)
class InstructionInstallResult:
    """Result of installing or removing an agent instruction snippet."""

    path: Path
    status: str


@dataclass(frozen=True, slots=True)
class SkillInstallResult:
    """Result of installing or removing the managed Synapse skill."""

    path: Path
    status: str
