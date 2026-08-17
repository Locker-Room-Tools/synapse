"""Resolution of adapter paths, including environment home overrides."""

import os
from pathlib import Path

from synapse.cli.adapters.model import PathSpec
from synapse.core.workspace import normalize_workspace_path


def resolve_user_path(spec: PathSpec) -> Path:
    """Resolve a user-scope path, honouring the adapter's home override."""
    if spec.env_var_full and (target := os.environ.get(spec.env_var_full)):
        return Path(target).expanduser()
    if spec.env_var and (override := os.environ.get(spec.env_var)):
        prefix = spec.env_prefix or ""
        suffix = Path(spec.path.removeprefix(prefix))
        return Path(override).expanduser().resolve() / suffix
    return Path(spec.path).expanduser()


def resolve_project_path(spec: PathSpec, workspace_path: str | Path) -> Path:
    """Resolve a project-scope path relative to the workspace root."""
    return normalize_workspace_path(workspace_path) / spec.path
