"""Shared CLI test fixtures."""

from pathlib import Path

import pytest

from synapse.cli.adapters import ADAPTERS, AgentAdapter, PathSpec


def adapter_user_specs(adapter: AgentAdapter) -> tuple[PathSpec, ...]:
    """Return the user-scope PathSpecs for one adapter."""
    specs: list[PathSpec | None] = [adapter.mcp.user, adapter.global_skill]
    if adapter.global_instructions is not None:
        specs.append(adapter.global_instructions.location)
    return tuple(spec for spec in specs if spec is not None)


def adapter_project_specs(adapter: AgentAdapter) -> tuple[PathSpec, ...]:
    """Return the workspace-relative PathSpecs for one adapter."""
    specs: list[PathSpec | None] = [adapter.project_skill]
    if adapter.project_instructions is not None:
        specs.append(adapter.project_instructions.location)
    return tuple(spec for spec in specs if spec is not None)


def adapter_path_specs(adapter: AgentAdapter) -> tuple[PathSpec, ...]:
    """Return every configured PathSpec for one adapter."""
    return adapter_user_specs(adapter) + adapter_project_specs(adapter)


def adapter_env_vars() -> tuple[str, ...]:
    """Return every home-override variable any adapter reads."""
    names = {
        spec.env_var
        for adapter in ADAPTERS.values()
        for spec in adapter_path_specs(adapter)
        if spec.env_var
    }
    return tuple(sorted(names))


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Route every supported agent home to the temporary directory.

    The directory is deliberately not created, so tests can assert that a
    command wrote nothing by checking that it still does not exist.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for name in adapter_env_vars():
        monkeypatch.delenv(name, raising=False)
    return home
