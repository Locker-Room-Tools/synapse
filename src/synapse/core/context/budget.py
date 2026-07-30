"""Deterministic output-budget estimation and enforcement for context results."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

CHARS_PER_TOKEN = 4
MIN_TOKEN_BUDGET = 500
DEFAULT_TOKEN_BUDGET = 4000
MAX_TOKEN_BUDGET = 20000
_ESTIMATE_FIELD_RESERVE = 16


def clamp_token_budget(requested: int) -> int:
    """Clamp a caller-supplied token budget to the supported deterministic range."""
    return min(MAX_TOKEN_BUDGET, max(MIN_TOKEN_BUDGET, requested))


def estimate_tokens(text: str) -> int:
    """Conservative deterministic token estimate: ceil(chars / 4)."""
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def serialize(payload: dict[str, object]) -> str:
    """Serialize one payload compactly and deterministically (insertion order kept)."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class DropStep:
    """One reversible-in-metadata drop: removes a single item from the payload state."""

    category: str
    apply: Callable[[], bool]


def enforce_budget(
    assemble: Callable[[dict[str, object]], dict[str, object]],
    steps: Sequence[DropStep],
    minimal: Callable[[dict[str, object]], dict[str, object]],
    *,
    token_budget: int,
) -> str:
    """Serialize under the hard character budget, dropping low-priority items first.

    ``assemble`` rebuilds the payload from mutable state after each drop and embeds
    the supplied truncation metadata. When every droppable item is gone and the
    payload still exceeds the budget, ``minimal`` provides the smallest honest
    envelope (seeds and coverage summary). The budget is a hard cap on the exact
    returned string, estimated as chars/4 tokens.
    """
    char_budget = token_budget * CHARS_PER_TOKEN
    dropped: dict[str, int] = {}
    queue = list(steps)

    def truncation() -> dict[str, object]:
        payload: dict[str, object] = {
            "budget_tokens": token_budget,
            "estimated_tokens": 0,
            "complete": not dropped,
        }
        if dropped:
            payload["dropped"] = dict(dropped)
        return payload

    def finalize(payload: dict[str, object], meta: dict[str, object]) -> str:
        meta["estimated_tokens"] = estimate_tokens(serialize(payload))
        return serialize(payload)

    while True:
        meta = truncation()
        payload = assemble(meta)
        if len(serialize(payload)) + _ESTIMATE_FIELD_RESERVE <= char_budget:
            return finalize(payload, meta)
        while queue:
            step = queue.pop(0)
            if step.apply():
                dropped[step.category] = dropped.get(step.category, 0) + 1
                break
        else:
            meta = truncation()
            meta["reason"] = "hard-cap"
            return finalize(minimal(meta), meta)
