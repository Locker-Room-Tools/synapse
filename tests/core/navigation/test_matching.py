"""Literal term-matching primitives."""

from synapse.core.index import kind_rank
from synapse.core.models import SymbolKind
from synapse.core.navigation.matching import (
    TermMatch,
    effective_kind_rank,
    generic_limit,
    match_tier,
    name_matches,
    prefix_at_word_start,
    subtoken_match,
)
from tests.core.navigation.builders import make_symbol


def test_match_tiers_order_strongest_evidence_first() -> None:
    ordered = [
        TermMatch.EXACT,
        TermMatch.PREFIX,
        TermMatch.SUBSTRING,
        TermMatch.PATH,
        TermMatch.MAP,
    ]
    tiers = [match_tier(match) for match in ordered]
    assert tiers == sorted(tiers)
    assert len(set(tiers)) == len(tiers)


def test_name_matches_covers_own_and_qualified_name() -> None:
    symbol = make_symbol("py:m", "authenticate", "app/auth.py", qualified_name="Login.authenticate")
    assert name_matches(symbol, "authenticate")
    assert name_matches(symbol, "AUTH")
    assert name_matches(symbol, "login")
    assert not name_matches(symbol, "billing")


def test_prefix_at_word_start_recognizes_snake_and_camel() -> None:
    assert prefix_at_word_start("build_service", "build")
    assert prefix_at_word_start("rebuild_service", "service")
    assert prefix_at_word_start("LoginService", "service")
    assert not prefix_at_word_start("rebuild", "build")


def test_generic_limit_has_floor_and_ratio() -> None:
    assert generic_limit(0) == 25
    assert generic_limit(2000) == 25
    assert generic_limit(10_000) == 100


def test_effective_kind_rank_promotes_callable_values() -> None:
    arrow = make_symbol(
        "ts:arrow",
        "handler",
        "app/handler.ts",
        kind=SymbolKind.CONSTANT,
        signature="(req) => void",
    )
    plain_constant = make_symbol("ts:plain", "LIMIT", "app/config.ts", kind=SymbolKind.CONSTANT)
    assert effective_kind_rank(arrow) == kind_rank(SymbolKind.FUNCTION)
    assert effective_kind_rank(plain_constant) == kind_rank(SymbolKind.CONSTANT)


def test_subtoken_match_is_word_boundary_equality() -> None:
    """A term matches only as a whole camel/snake subtoken, never by containment."""
    assert subtoken_match("symbol_index_writer", "index")
    assert subtoken_match("SymbolIndexWriter", "index")
    assert subtoken_match("GetUserSessionAsync", "session")
    assert subtoken_match("handler_02", "handler")
    assert subtoken_match("handler", "handler")
    # Containment without a word boundary is not evidence.
    assert not subtoken_match("prehandlerish", "handler")
    assert not subtoken_match("session", "sess")
    assert not subtoken_match("SymbolIndexWriter", "symbolindex")


def test_subtoken_match_splits_acronym_runs_and_digits() -> None:
    """Acronym runs stay whole and digit groups are their own subtokens."""
    assert subtoken_match("HTTPServer", "http")
    assert subtoken_match("HTTPServer", "server")
    assert not subtoken_match("HTTPServer", "https")
    assert subtoken_match("parse_v2_payload", "v2")
    assert subtoken_match("Base64URL", "base64")
