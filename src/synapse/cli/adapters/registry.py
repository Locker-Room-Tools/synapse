"""Declarative registry of supported agent adapters."""

from synapse.cli.adapters.model import (
    AgentAdapter,
    ConfigFormat,
    ContainerShape,
    HookShape,
    HookTarget,
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
        project_skill=PathSpec(".claude/skills/synapse-code-context"),
        global_skill=PathSpec("~/.claude/skills/synapse-code-context"),
        hook=HookTarget(
            codec="claude-pre-bash",
            settings=PathSpec("~/.claude/settings.json"),
            shape=HookShape.NESTED,
            key_path=("hooks", "PreToolUse"),
            matcher="Bash",
            timeout=10,
        ),
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
        skill_files=(
            "SKILL.md",
            "agents/openai.yaml",
            "references/evidence-semantics.md",
        ),
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
    # Cline docs advertise ~/.cline/mcp.json, but the shared resolver reads
    # ~/.cline/data/settings/cline_mcp_settings.json (cline#11671). Rules and skills
    # live directly under ~/.cline/, outside the CLINE_DATA_DIR subtree.
    "cline": AgentAdapter(
        id="cline",
        display_name="Cline",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcpServers",),
            project=".cline/mcp.json",
            user=PathSpec(
                "~/.cline/data/settings/cline_mcp_settings.json",
                "CLINE_DATA_DIR",
                "~/.cline/data/",
                env_var_full="CLINE_MCP_SETTINGS_PATH",
            ),
        ),
        project_instructions=InstructionTarget(
            PathSpec(".cline/rules/synapse.md"), InstructionMode.OWNED
        ),
        global_instructions=InstructionTarget(
            PathSpec("~/.cline/rules/synapse.md"),
            InstructionMode.OWNED,
        ),
        project_skill=PathSpec(".cline/skills/synapse-code-context"),
        global_skill=PathSpec("~/.cline/skills/synapse-code-context"),
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
        # Qwen documents context injection on PreToolUse but only worked-examples it
        # alongside a deny decision, so the allow path is unconfirmed upstream.
        hook=HookTarget(
            codec="qwen-pre-bash",
            settings=PathSpec("~/.qwen/settings.json", "QWEN_HOME", "~/.qwen/"),
            shape=HookShape.NESTED,
            key_path=("hooks", "PreToolUse"),
            matcher="^run_shell_command$",
            timeout=10000,
        ),
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
    "kimi": AgentAdapter(
        id="kimi",
        display_name="Kimi Code CLI",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcpServers",),
            project=".kimi-code/mcp.json",
            user=PathSpec("~/.kimi-code/mcp.json", "KIMI_CODE_HOME", "~/.kimi-code/"),
        ),
        project_instructions=InstructionTarget(PathSpec("AGENTS.md")),
        global_instructions=InstructionTarget(
            PathSpec("~/.kimi-code/AGENTS.md", "KIMI_CODE_HOME", "~/.kimi-code/")
        ),
        project_skill=PathSpec(".kimi-code/skills/synapse-code-context"),
        global_skill=PathSpec(
            "~/.kimi-code/skills/synapse-code-context", "KIMI_CODE_HOME", "~/.kimi-code/"
        ),
    ),
    "droid": AgentAdapter(
        id="droid",
        display_name="Factory Droid",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcpServers",),
            project=".factory/mcp.json",
            user=PathSpec("~/.factory/mcp.json"),
        ),
        project_instructions=InstructionTarget(PathSpec("AGENTS.md")),
        global_instructions=InstructionTarget(PathSpec("~/.factory/AGENTS.md")),
        project_skill=PathSpec(".factory/skills/synapse-code-context"),
        global_skill=PathSpec("~/.factory/skills/synapse-code-context"),
    ),
    # Crush requires "type" on every MCP entry and rejects unknown keys
    # (additionalProperties: false), so the payload stays exactly these fields.
    "crush": AgentAdapter(
        id="crush",
        display_name="Crush",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcp",),
            extra_fields=(("type", "stdio"),),
            project="crush.json",
            user=PathSpec("~/.config/crush/crush.json", "XDG_CONFIG_HOME", "~/.config/"),
        ),
        project_instructions=InstructionTarget(PathSpec("CRUSH.md")),
        global_instructions=InstructionTarget(
            PathSpec("~/.config/crush/CRUSH.md", "XDG_CONFIG_HOME", "~/.config/")
        ),
        project_skill=PathSpec(".crush/skills/synapse-code-context"),
        global_skill=PathSpec(
            "~/.config/crush/skills/synapse-code-context", "XDG_CONFIG_HOME", "~/.config/"
        ),
        hook=HookTarget(
            codec="crush-pre-bash",
            settings=PathSpec("~/.config/crush/crush.json", "XDG_CONFIG_HOME", "~/.config/"),
            shape=HookShape.FLAT,
            key_path=("hooks", "PreToolUse"),
            matcher="^bash$",
            timeout=10,
        ),
    ),
    # "amp.mcpServers" is one literal settings key containing a dot, not a nested path.
    "amp": AgentAdapter(
        id="amp",
        display_name="Amp",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("amp.mcpServers",),
            project=".amp/settings.json",
            user=PathSpec("~/.config/amp/settings.json", "XDG_CONFIG_HOME", "~/.config/"),
        ),
        project_instructions=InstructionTarget(PathSpec("AGENTS.md")),
        global_instructions=InstructionTarget(
            PathSpec("~/.config/amp/AGENTS.md", "XDG_CONFIG_HOME", "~/.config/")
        ),
        project_skill=PathSpec(".agents/skills/synapse-code-context"),
        global_skill=PathSpec(
            "~/.config/amp/skills/synapse-code-context", "XDG_CONFIG_HOME", "~/.config/"
        ),
    ),
    # Zed picks the first matching instruction file from a fixed list; ".rules" ranks
    # first, so the block is guaranteed to be the file Zed actually reads.
    "zed": AgentAdapter(
        id="zed",
        display_name="Zed",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("context_servers",),
            project=".zed/settings.json",
            user=PathSpec("~/.config/zed/settings.json", "XDG_CONFIG_HOME", "~/.config/"),
        ),
        project_instructions=InstructionTarget(PathSpec(".rules")),
        global_instructions=InstructionTarget(
            PathSpec("~/.config/zed/AGENTS.md", "XDG_CONFIG_HOME", "~/.config/")
        ),
        project_skill=PathSpec(".agents/skills/synapse-code-context"),
        global_skill=PathSpec("~/.agents/skills/synapse-code-context"),
    ),
    "antigravity": AgentAdapter(
        id="antigravity",
        display_name="Google Antigravity",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcpServers",),
            project=".agents/mcp_config.json",
            user=PathSpec("~/.gemini/config/mcp_config.json"),
        ),
        project_instructions=InstructionTarget(
            PathSpec(".agents/rules/synapse.md"), InstructionMode.OWNED
        ),
        global_instructions=InstructionTarget(PathSpec("~/.gemini/GEMINI.md")),
        project_skill=PathSpec(".agents/skills/synapse-code-context"),
        global_skill=PathSpec("~/.gemini/config/skills/synapse-code-context"),
    ),
    # Goose documents no project config, so MCP config is global-only. Its stdio
    # entries spell the executable "cmd" and require a non-defaulted "enabled".
    "goose": AgentAdapter(
        id="goose",
        display_name="Goose",
        mcp=McpTarget(
            fmt=ConfigFormat.YAML,
            shape=ContainerShape.MAPPING,
            key_path=("extensions",),
            command_field="cmd",
            extra_fields=(("type", "stdio"), ("enabled", True)),
            user=PathSpec("~/.config/goose/config.yaml", "XDG_CONFIG_HOME", "~/.config/"),
        ),
        default_scope="user",
        project_instructions=InstructionTarget(PathSpec(".goosehints")),
        global_instructions=InstructionTarget(
            PathSpec("~/.config/goose/.goosehints", "XDG_CONFIG_HOME", "~/.config/")
        ),
        project_skill=PathSpec(".agents/skills/synapse-code-context"),
        global_skill=PathSpec("~/.agents/skills/synapse-code-context"),
    ),
    # OpenClaw has a single global config, and its "workspace" is the agent home
    # (~/.openclaw/workspace), not the repository, so there are no project targets.
    "openclaw": AgentAdapter(
        id="openclaw",
        display_name="OpenClaw",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcp", "servers"),
            user=PathSpec("~/.openclaw/openclaw.json", "OPENCLAW_HOME", "~/.openclaw/"),
        ),
        default_scope="user",
        global_skill=PathSpec(
            "~/.openclaw/skills/synapse-code-context", "OPENCLAW_HOME", "~/.openclaw/"
        ),
    ),
    # The VS Code user mcp.json path is undocumented and moves with profiles and
    # builds, so this adapter is project-scope only. Root key is "servers".
    "copilot-vscode": AgentAdapter(
        id="copilot-vscode",
        display_name="GitHub Copilot (VS Code)",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("servers",),
            extra_fields=(("type", "stdio"),),
            project=".vscode/mcp.json",
        ),
        project_instructions=InstructionTarget(PathSpec(".github/copilot-instructions.md")),
        project_skill=PathSpec(".github/skills/synapse-code-context"),
    ),
    # Roo's global MCP file lives under a user-relocatable storage base, so only the
    # project MCP path is safe to write; the global skills dir is a plain path.
    "roo": AgentAdapter(
        id="roo",
        display_name="Roo Code",
        mcp=McpTarget(
            fmt=ConfigFormat.JSON,
            shape=ContainerShape.MAPPING,
            key_path=("mcpServers",),
            project=".roo/mcp.json",
        ),
        project_instructions=InstructionTarget(
            PathSpec(".roo/rules/synapse.md"), InstructionMode.OWNED
        ),
        project_skill=PathSpec(".roo/skills/synapse-code-context"),
        global_skill=PathSpec("~/.roo/skills/synapse-code-context"),
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
