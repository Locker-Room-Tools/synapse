"""Agent-independent logic for the suggest-only shell-exploration nudge."""

import re

from synapse.core.workspace import (
    DEFAULT_DB_NAME,
    data_dir_path,
    detect_workspace_root,
)

_EXPLORATION_PATTERN = re.compile(r"(?:^|[|;&(]\s*)(?:command\s+)?(?:grep|rg|cat|find|tree)\b")

REMINDER = (
    "This workspace is indexed by Synapse. For code navigation prefer the MCP tools over "
    "shell exploration: translate the task into repository terms, call synapse_orient "
    "for ranked matches with compact handles, then synapse_inspect with 2-3 "
    "facet-diverse handles — following returned relation handles for facets still "
    "open — for definitions, bounded source, call-proven callers/callees, and "
    "neutral refs_in/refs_out. Shell search remains fine for exact text or content "
    "Synapse does not index."
)


def workspace_is_indexed(cwd: str) -> bool:
    """Report whether the workspace containing ``cwd`` has a Synapse index."""
    workspace_root = detect_workspace_root(cwd)
    return (data_dir_path(workspace_root) / DEFAULT_DB_NAME).exists()


def should_remind(command: str, cwd: str) -> bool:
    """Report whether a shell command warrants the Synapse navigation reminder."""
    if not _EXPLORATION_PATTERN.search(command):
        return False
    return workspace_is_indexed(cwd)
