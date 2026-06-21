"""Smoke tests for the Synapse package."""

import synapse


def test_package_exposes_version() -> None:
    """Package import exposes the current project version."""
    assert synapse.__version__ == "0.1.0"