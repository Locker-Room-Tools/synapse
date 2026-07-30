"""Tests for deterministic keyword extraction."""

from synapse.core.context import extract_keywords


def test_identifier_like_tokens_are_detected() -> None:
    keywords = extract_keywords("How does WatchWorker.apply_batch reach replace_symbols_for_file?")
    assert keywords.identifiers == (
        "WatchWorker.apply_batch",
        "replace_symbols_for_file",
    )
    assert "watch" in keywords.terms
    assert "apply" in keywords.terms
    assert "how" not in keywords.terms
    assert "does" not in keywords.terms


def test_quoted_tokens_become_identifiers() -> None:
    keywords = extract_keywords("Where is `serve` registered?")
    assert keywords.identifiers == ("serve",)


def test_plain_prose_yields_terms_only() -> None:
    keywords = extract_keywords("how does the watch daemon apply a change")
    assert keywords.identifiers == ()
    assert keywords.terms == ("watch", "daemon", "apply", "change")


def test_extraction_is_deterministic_and_deduplicated() -> None:
    question = "trace index_workspace and index_workspace again"
    first = extract_keywords(question)
    second = extract_keywords(question)
    assert first == second
    assert first.identifiers == ("index_workspace",)
    assert first.terms.count("index") == 1


def test_short_fragments_and_acronyms() -> None:
    keywords = extract_keywords("Is the DB id in the AST?")
    assert "db" in keywords.terms
    assert "ast" in keywords.terms
    assert "id" not in keywords.terms


def test_empty_question_yields_no_keywords() -> None:
    keywords = extract_keywords("")
    assert keywords.identifiers == ()
    assert keywords.terms == ()
