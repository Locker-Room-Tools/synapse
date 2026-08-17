"""Suggest-only pre-shell hooks: shared decision logic, wire codecs, installer."""

from synapse.cli.hooks.codecs import CODECS, HookCodec, codec_choices, run_hook
from synapse.cli.hooks.core import REMINDER, should_remind, workspace_is_indexed
from synapse.cli.hooks.install import (
    HookInstallResult,
    hook_command,
    install_hook,
    remove_hook,
    resolve_hook_settings_path,
)

__all__ = [
    "CODECS",
    "REMINDER",
    "HookCodec",
    "HookInstallResult",
    "codec_choices",
    "hook_command",
    "install_hook",
    "remove_hook",
    "resolve_hook_settings_path",
    "run_hook",
    "should_remind",
    "workspace_is_indexed",
]
