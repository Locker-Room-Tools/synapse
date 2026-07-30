"""Deterministic output-budget estimation and enforcement for context results.

Contract: ``token_budget`` is an ESTIMATED token budget at a fixed 4 characters per
token. The hard, tested guarantee is on characters: the final serialized result never
exceeds ``token_budget * CHARS_PER_TOKEN`` characters. ``estimated_tokens`` in the
result is an approximation, never an exact model-token count.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

CHARS_PER_TOKEN = 4
MIN_TOKEN_BUDGET = 500
DEFAULT_TOKEN_BUDGET = 4000
MAX_TOKEN_BUDGET = 20000


def clamp_token_budget(requested: int) -> int:
    """Clamp a caller-supplied token budget to the supported deterministic range."""
    return min(MAX_TOKEN_BUDGET, max(MIN_TOKEN_BUDGET, requested))


def estimate_tokens(text: str) -> int:
    """Conservative deterministic token estimate: ceil(chars / 4). An estimate only."""
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def serialize(payload: dict[str, object]) -> str:
    """Serialize one payload compactly and deterministically (insertion order kept)."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class DropStep:
    """One deterministic drop: removes a single item from the payload state."""

    category: str
    apply: Callable[[], bool]


def enforce_budget(
    assemble: Callable[[dict[str, object]], dict[str, object]],
    steps: Sequence[DropStep],
    minimal: Callable[[dict[str, object]], dict[str, object]],
    *,
    token_budget: int,
) -> str:
    """Serialize under the hard character cap, dropping low-priority items first.

    The cap ``token_budget * CHARS_PER_TOKEN`` is checked against the exact string
    that is returned, at every stage. ``assemble`` rebuilds the payload from mutable
    state after each drop and must embed the supplied truncation mapping by
    reference. When every droppable item is gone, deterministic shrink stages apply:
    the ``minimal`` envelope, then that envelope with trailing seed entries removed
    one by one, then a fixed tiny envelope that always fits. The result is always
    valid JSON.
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
        final = finalize(assemble(meta), meta)
        if len(final) <= char_budget:
            return final
        for _ in range(len(queue)):
            step = queue.pop(0)
            if step.apply():
                dropped[step.category] = dropped.get(step.category, 0) + 1
                break
        else:
            break

    meta = truncation()
    meta["reason"] = "hard-cap"
    payload = minimal(meta)
    final = finalize(payload, meta)
    if len(final) <= char_budget:
        return final
    seeds = payload.get("seeds")
    while isinstance(seeds, list) and seeds:
        seeds.pop()
        final = finalize(payload, meta)
        if len(final) <= char_budget:
            return final

    ultimate: dict[str, object] = {
        "truncation": {
            "budget_tokens": token_budget,
            "complete": False,
            "reason": "hard-cap",
        }
    }
    return serialize(ultimate)
