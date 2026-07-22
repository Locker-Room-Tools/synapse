"""Tests for explicit tree-sitter grammar installation."""

import pytest

from synapse.cli import grammars
from synapse.core.languages import tree_sitter_language_names


def test_install_grammars_prefetches_the_supported_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The network-enabled installer prepares every configured grammar once."""
    seen: list[str] = []
    monkeypatch.setattr(grammars, "prefetch", lambda names: seen.extend(names))

    installed = grammars.install_grammars()

    assert installed == tree_sitter_language_names()
    assert seen == list(installed)
