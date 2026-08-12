"""Shared invariants for the page metadata every bounded read projection returns."""

from typing import Any


def assert_page_is_consistent(page: dict[str, Any]) -> None:
    """A page can withhold rows, but it can never return more than it counted.

    `returned` describes the rows on this page and `total` the whole matching set, so
    `returned > total` means the two were produced by different definitions of "match" —
    exactly what a tokenizer-based candidate query slipping past a literal count causes.
    """
    returned = int(page["returned"])
    total = int(page["total"])
    assert 0 <= returned <= total, f"page returned {returned} of a claimed total {total}"
    assert page["has_more"] is (int(page["offset"]) + returned < total)
