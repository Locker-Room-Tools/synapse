"""Tests for offline tree-sitter grammar loading."""

import pytest

from synapse.core import grammars


def test_get_installed_language_rejects_missing_grammar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parse path fails locally instead of triggering an implicit download."""
    grammars.get_installed_language.cache_clear()
    monkeypatch.setattr(grammars, "downloaded_languages", lambda: [])

    with pytest.raises(grammars.GrammarNotInstalledError, match="synapse grammars install"):
        grammars.get_installed_language("python")


def test_get_installed_language_loads_cached_grammar(monkeypatch: pytest.MonkeyPatch) -> None:
    """A grammar reported by the local cache is delegated to the language pack."""
    sentinel = object()
    grammars.get_installed_language.cache_clear()
    monkeypatch.setattr(grammars, "downloaded_languages", lambda: ["python"])
    monkeypatch.setattr(grammars, "get_language", lambda name: sentinel)

    assert grammars.get_installed_language("python") is sentinel
    grammars.get_installed_language.cache_clear()
