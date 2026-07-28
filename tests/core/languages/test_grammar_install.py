"""Tests for explicit tree-sitter grammar installation."""

from types import SimpleNamespace

import pytest

from synapse.core.languages import grammar_install, tree_sitter_language_names


def test_install_grammars_prefetches_the_supported_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The network-enabled installer prepares every configured grammar once."""
    seen: list[str] = []
    cache_clears: list[bool] = []
    monkeypatch.setattr(grammar_install, "prefetch", lambda names: seen.extend(names))
    monkeypatch.setattr(
        grammar_install,
        "get_installed_language",
        SimpleNamespace(cache_clear=lambda: cache_clears.append(True)),
    )

    installed = grammar_install.install_grammars()

    assert installed == tree_sitter_language_names()
    assert seen == list(installed)
    assert cache_clears == [True]


def test_missing_grammars_preserves_supported_registry_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only absent parsers are reported, in the stable supported-language order."""
    supported = tree_sitter_language_names()
    monkeypatch.setattr(grammar_install, "downloaded_languages", lambda: [supported[1]])

    missing = grammar_install.missing_grammars()

    assert missing == tuple(name for name in supported if name != supported[1])


def test_missing_grammars_returns_empty_when_cache_is_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fully populated local parser cache needs no installation."""
    monkeypatch.setattr(
        grammar_install,
        "downloaded_languages",
        tree_sitter_language_names,
    )

    assert grammar_install.missing_grammars() == ()
