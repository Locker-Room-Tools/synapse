"""Per-agent wire codecs for the suggest-only pre-shell hook.

Every supported agent passes the same three facts on stdin — tool name, shell
command, working directory — so only the tool-name spelling and the stdout shape
differ. Each codec is fail-open: a caller that hits any exception emits nothing
and exits 0, leaving the agent's tool call untouched.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TextIO

from synapse.cli.adapters.model import JsonObject
from synapse.cli.hooks.core import REMINDER, should_remind

_ALLOW_REASON = "Synapse navigation reminder; the command is not restricted."


def _claude_output() -> JsonObject:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": REMINDER,
        }
    }


def _qwen_output() -> JsonObject:
    """Qwen requires an explicit permission decision alongside the context."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": _ALLOW_REASON,
            "additionalContext": REMINDER,
        }
    }


def _crush_output() -> JsonObject:
    """Crush appends ``context`` to what the model sees.

    ``decision`` is deliberately omitted: that means "no opinion", so the call
    still goes through Crush's normal permission prompt.
    """
    return {"version": 1, "context": REMINDER}


@dataclass(frozen=True, slots=True)
class HookCodec:
    """Wire contract for one agent's pre-shell hook."""

    name: str
    tool_names: frozenset[str]
    render: Callable[[], JsonObject]


CODECS: dict[str, HookCodec] = {
    "claude-pre-bash": HookCodec(
        name="claude-pre-bash",
        tool_names=frozenset({"Bash"}),
        render=_claude_output,
    ),
    "qwen-pre-bash": HookCodec(
        name="qwen-pre-bash",
        tool_names=frozenset({"run_shell_command"}),
        render=_qwen_output,
    ),
    "crush-pre-bash": HookCodec(
        name="crush-pre-bash",
        tool_names=frozenset({"bash"}),
        render=_crush_output,
    ),
}


def codec_choices() -> tuple[str, ...]:
    """Return supported hook codec names for argparse choices."""
    return tuple(sorted(CODECS))


def run_hook(codec_name: str, stdin: TextIO, stdout: TextIO) -> int:
    """Emit a non-blocking Synapse reminder for shell exploration commands.

    Never blocks the tool call and never fails: the hook only adds context, so any
    parsing or lookup problem degrades to silence.
    """
    try:
        codec = CODECS[codec_name]
        payload = json.load(stdin)
        if payload.get("tool_name") not in codec.tool_names:
            return 0
        command = str(payload.get("tool_input", {}).get("command", ""))
        if not should_remind(command, str(payload.get("cwd", "."))):
            return 0
        json.dump(codec.render(), stdout)
    except Exception:  # noqa: BLE001 - a hook must never break the user's tool call
        return 0
    return 0
