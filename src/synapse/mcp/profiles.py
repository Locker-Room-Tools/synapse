"""Profile-tiered tool registry: which tools each MCP surface exposes."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar


class ToolProfile(StrEnum):
    """Public MCP tool surfaces.

    DEFAULT is the minimal coding-agent surface; FULL adds administrative,
    configuration, and primitive projection tools.
    """

    DEFAULT = "default"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One registered tool function and the lowest profile that exposes it."""

    func: Callable[..., object]
    tier: ToolProfile
    structured_output: bool | None = None


_SPECS: list[ToolSpec] = []

_ToolFunc = TypeVar("_ToolFunc", bound=Callable[..., object])


def tool(
    tier: ToolProfile = ToolProfile.FULL,
    *,
    structured_output: bool | None = None,
) -> Callable[[_ToolFunc], _ToolFunc]:
    """Record a tool for profile-based registration; the function is returned unchanged.

    structured_output=False keeps a result out of structuredContent so the payload is
    serialized exactly once on the wire (required for budget-bound string results).
    """

    def record(func: _ToolFunc) -> _ToolFunc:
        _SPECS.append(ToolSpec(func=func, tier=tier, structured_output=structured_output))
        return func

    return record


def _ensure_tools_loaded() -> None:
    import synapse.mcp.tools  # noqa: F401  # registration happens at import


def specs_for_profile(profile: ToolProfile) -> tuple[ToolSpec, ...]:
    """Return the tool specs one profile exposes, in declaration order."""
    _ensure_tools_loaded()
    if profile is ToolProfile.FULL:
        return tuple(_SPECS)
    return tuple(spec for spec in _SPECS if spec.tier is ToolProfile.DEFAULT)


def tool_names_for_profile(profile: ToolProfile) -> tuple[str, ...]:
    """Return the public tool names one profile exposes, in declaration order."""
    return tuple(spec.func.__name__ for spec in specs_for_profile(profile))
