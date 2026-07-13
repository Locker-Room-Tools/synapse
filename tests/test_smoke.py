"""Smoke tests for the Synapse package."""

import tomllib
from importlib import metadata
from pathlib import Path

import synapse


def test_package_exposes_installed_version() -> None:
    """Package import exposes the installed distribution version."""
    assert synapse.__version__ == metadata.version("synapse-mcp")


def test_version_matches_pyproject() -> None:
    """The installed version tracks pyproject.toml (guards stale editable installs)."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    assert synapse.__version__ == project["version"]
