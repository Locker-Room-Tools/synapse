"""Declarative registry of supported agent adapters."""

from synapse.cli.adapters.model import (
    AgentAdapter,
    ConfigFormat,
    ContainerShape,
    InstructionMode,
    InstructionTarget,
    McpTarget,
    PathSpec,
    PayloadStyle,
)

ADAPTERS: dict[str, AgentAdapter] = {
    "claude-code": AgentAdapter(
        id="claude-code",
        display_name="Claude Code",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcpServers",),
            project=".mcp.json",
            user=PathSpec("~/.claude.json"),
        ),
        project_instructions=InstructionTarget(PathSpec("CLAUDE.md")),
        global_instructions=InstructionTarget(PathSpec("~/.claude/CLAUDE.md")),
        global_skill=PathSpec("~/.claude/skills/synapse-code-context"),
        supports_hook=True,
    ),
    "codex": AgentAdapter(
        id="codex",
        display_name="Codex",
        mcp=McpTarget(
            fmt=ConfigFormat.TOML,
            shape=ContainerShape.MAPPING,
            key_path=("mcp_servers",),
            project=".codex/config.toml",
            user=PathSpec("~/.codex/config.toml", "CODEX_HOME", "~/.codex/"),
        ),
        project_instructions=InstructionTarget(PathSpec("AGENTS.md")),
        global_instructions=InstructionTarget(
            PathSpec("~/.codex/AGENTS.md", "CODEX_HOME", "~/.codex/")
        ),
        global_skill=PathSpec("~/.codex/skills/synapse-code-context", "CODEX_HOME", "~/.codex/"),
        skill_files=("SKILL.md", "agents/openai.yaml"),
        warn_legacy_user_config=True,
    ),
    "opencode": AgentAdapter(
        id="opencode",
        display_name="OpenCode",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcp",),
            payload_style=PayloadStyle.COMMAND_LIST,
            extra_fields=(("type", "local"), ("enabled", True)),
            document_defaults=(("$schema", "https://opencode.ai/config.json"),),
            project="opencode.json",
            user=PathSpec("~/.config/opencode/opencode.json", "XDG_CONFIG_HOME", "~/.config/"),
        ),
        project_instructions=InstructionTarget(PathSpec("AGENTS.md")),
        global_instructions=InstructionTarget(
            PathSpec("~/.config/opencode/AGENTS.md", "XDG_CONFIG_HOME", "~/.config/")
        ),
        global_skill=PathSpec(
            "~/.config/opencode/skills/synapse-code-context",
            "XDG_CONFIG_HOME",
            "~/.config/",
        ),
    ),
    # Hermes MCP config is global-only; SOUL.md is personality/tone and is never written.
    "hermes": AgentAdapter(
        id="hermes",
        display_name="Hermes",
        mcp=McpTarget(
            fmt=ConfigFormat.YAML,
            shape=ContainerShape.MAPPING,
            key_path=("mcp_servers",),
            user=PathSpec("~/.hermes/config.yaml", "HERMES_HOME", "~/.hermes/"),
        ),
        default_scope="user",
        project_instructions=InstructionTarget(PathSpec(".hermes.md")),
        global_skill=PathSpec("~/.hermes/skills/synapse-code-context", "HERMES_HOME", "~/.hermes/"),
    ),
    "gemini": AgentAdapter(
        id="gemini",
        display_name="Gemini CLI",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcpServers",),
            project=".gemini/settings.json",
            user=PathSpec("~/.gemini/settings.json"),
        ),
        project_instructions=InstructionTarget(PathSpec("GEMINI.md")),
        global_instructions=InstructionTarget(PathSpec("~/.gemini/GEMINI.md")),
        project_skill=PathSpec(".gemini/skills/synapse-code-context"),
        global_skill=PathSpec("~/.gemini/skills/synapse-code-context"),
    ),
    "copilot": AgentAdapter(
        id="copilot",
        display_name="GitHub Copilot CLI",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcpServers",),
            extra_fields=(("type", "local"), ("tools", ("*",))),
            project=".github/mcp.json",
            user=PathSpec("~/.copilot/mcp-config.json", "COPILOT_HOME", "~/.copilot/"),
        ),
        project_instructions=InstructionTarget(PathSpec(".github/copilot-instructions.md")),
        global_instructions=InstructionTarget(
            PathSpec("~/.copilot/copilot-instructions.md", "COPILOT_HOME", "~/.copilot/")
        ),
        project_skill=PathSpec(".github/skills/synapse-code-context"),
        global_skill=PathSpec(
            "~/.copilot/skills/synapse-code-context", "COPILOT_HOME", "~/.copilot/"
        ),
    ),
    # Cursor User Rules are UI-only, so there is no global instruction file to own.
    "cursor": AgentAdapter(
        id="cursor",
        display_name="Cursor",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcpServers",),
            extra_fields=(("type", "stdio"),),
            project=".cursor/mcp.json",
            user=PathSpec("~/.cursor/mcp.json"),
        ),
        project_instructions=InstructionTarget(
            PathSpec(".cursor/rules/synapse.mdc"),
            InstructionMode.OWNED,
            (("alwaysApply", "true"),),
        ),
        project_skill=PathSpec(".cursor/skills/synapse-code-context"),
        global_skill=PathSpec("~/.cursor/skills/synapse-code-context"),
    ),
    # Windsurf documents no project MCP path, so MCP config is global-only.
    "windsurf": AgentAdapter(
        id="windsurf",
        display_name="Windsurf",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcpServers",),
            user=PathSpec("~/.codeium/windsurf/mcp_config.json"),
        ),
        default_scope="user",
        project_instructions=InstructionTarget(
            PathSpec(".windsurf/rules/synapse.md"),
            InstructionMode.OWNED,
            (("trigger", "always_on"),),
        ),
        global_instructions=InstructionTarget(
            PathSpec("~/.codeium/windsurf/memories/global_rules.md")
        ),
        project_skill=PathSpec(".windsurf/skills/synapse-code-context"),
        global_skill=PathSpec("~/.codeium/windsurf/skills/synapse-code-context"),
    ),
    # CLINE_DATA_DIR replaces ~/.cline/data/ only, so it never moves ~/.cline/mcp.json.
    "cline": AgentAdapter(
        id="cline",
        display_name="Cline CLI",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcpServers",),
            project=".cline/mcp.json",
            user=PathSpec("~/.cline/mcp.json"),
        ),
        project_instructions=InstructionTarget(
            PathSpec(".cline/rules/synapse.md"), InstructionMode.OWNED
        ),
        global_instructions=InstructionTarget(
            PathSpec(
                "~/.cline/data/settings/rules/synapse.md",
                "CLINE_DATA_DIR",
                "~/.cline/data/",
            ),
            InstructionMode.OWNED,
        ),
        project_skill=PathSpec(".cline/skills/synapse-code-context"),
        global_skill=PathSpec(
            "~/.cline/data/settings/skills/synapse-code-context",
            "CLINE_DATA_DIR",
            "~/.cline/data/",
        ),
    ),
    "kiro": AgentAdapter(
        id="kiro",
        display_name="Kiro CLI",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcpServers",),
            project=".kiro/settings/mcp.json",
            user=PathSpec("~/.kiro/settings/mcp.json", "KIRO_HOME", "~/.kiro/"),
        ),
        project_instructions=InstructionTarget(
            PathSpec(".kiro/steering/synapse.md"),
            InstructionMode.OWNED,
            (("inclusion", "always"),),
        ),
        global_instructions=InstructionTarget(
            PathSpec("~/.kiro/steering/synapse.md", "KIRO_HOME", "~/.kiro/"),
            InstructionMode.OWNED,
            (("inclusion", "always"),),
        ),
        project_skill=PathSpec(".kiro/skills/synapse-code-context"),
        global_skill=PathSpec("~/.kiro/skills/synapse-code-context", "KIRO_HOME", "~/.kiro/"),
    ),
    "qwen": AgentAdapter(
        id="qwen",
        display_name="Qwen Code",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcpServers",),
            project=".qwen/settings.json",
            user=PathSpec("~/.qwen/settings.json", "QWEN_HOME", "~/.qwen/"),
        ),
        project_instructions=InstructionTarget(PathSpec("QWEN.md")),
        global_instructions=InstructionTarget(PathSpec("~/.qwen/QWEN.md", "QWEN_HOME", "~/.qwen/")),
        project_skill=PathSpec(".qwen/skills/synapse-code-context"),
        global_skill=PathSpec("~/.qwen/skills/synapse-code-context", "QWEN_HOME", "~/.qwen/"),
    ),
    # Continue config.yaml requires name/version/schema, so Synapse never creates it.
    # The project target is a standalone block file Synapse owns end to end.
    "continue": AgentAdapter(
        id="continue",
        display_name="Continue CLI",
        mcp=McpTarget(
            fmt=ConfigFormat.YAML,
            shape=ContainerShape.LIST,
            key_path=("mcpServers",),
            name_field="name",
            document_defaults=(
                ("name", "Synapse"),
                ("version", "0.0.1"),
                ("schema", "v1"),
            ),
            project=".continue/mcpServers/synapse.yaml",
            user=PathSpec("~/.continue/config.yaml", "CONTINUE_GLOBAL_DIR", "~/.continue/"),
            user_requires_existing=True,
        ),
        project_instructions=InstructionTarget(
            PathSpec(".continue/rules/synapse.md"), InstructionMode.OWNED
        ),
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


__all__ = ["ADAPTERS", "adapter_choices", "get_adapter"]
