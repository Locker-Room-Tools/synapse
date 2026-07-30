"""Tests for deterministic budget estimation and enforcement."""

from collections.abc import Callable

from synapse.core.context.budget import (
    CHARS_PER_TOKEN,
    MAX_TOKEN_BUDGET,
    MIN_TOKEN_BUDGET,
    DropStep,
    clamp_token_budget,
    enforce_budget,
    estimate_tokens,
    serialize,
)


def test_estimate_tokens_is_conservative_ceiling() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


def test_clamp_token_budget_bounds() -> None:
    assert clamp_token_budget(1) == MIN_TOKEN_BUDGET
    assert clamp_token_budget(10**9) == MAX_TOKEN_BUDGET
    assert clamp_token_budget(4000) == 4000


def test_serialize_is_compact_and_ordered() -> None:
    assert serialize({"b": 1, "a": [1, 2]}) == '{"b":1,"a":[1,2]}'


def _make_state_and_steps(item_count: int) -> tuple[list[str], list[DropStep]]:
    items = [f"item-{i:03d}-{'x' * 40}" for i in range(item_count)]

    def drop_last() -> bool:
        if items:
            items.pop()
            return True
        return False

    steps = [DropStep(category="items", apply=drop_last) for _ in range(item_count)]
    return items, steps


def test_enforce_budget_returns_untruncated_payload_when_it_fits() -> None:
    items, steps = _make_state_and_steps(3)

    def assemble(truncation: dict[str, object]) -> dict[str, object]:
        return {"items": list(items), "truncation": truncation}

    def minimal(truncation: dict[str, object]) -> dict[str, object]:
        return {"truncation": truncation}

    text = enforce_budget(assemble, steps, minimal, token_budget=MIN_TOKEN_BUDGET)
    assert len(items) == 3
    assert '"complete":true' in text
    assert '"dropped"' not in text


def test_enforce_budget_drops_items_until_the_hard_cap_holds() -> None:
    items, steps = _make_state_and_steps(200)

    def assemble(truncation: dict[str, object]) -> dict[str, object]:
        return {"items": list(items), "truncation": truncation}

    def minimal(truncation: dict[str, object]) -> dict[str, object]:
        return {"truncation": truncation}

    text = enforce_budget(assemble, steps, minimal, token_budget=MIN_TOKEN_BUDGET)
    assert len(text) <= MIN_TOKEN_BUDGET * CHARS_PER_TOKEN
    assert 0 < len(items) < 200
    assert '"complete":false' in text
    assert '"dropped":{"items":' in text
    assert estimate_tokens(text) <= MIN_TOKEN_BUDGET


def test_enforce_budget_falls_back_to_minimal_envelope() -> None:
    payload_filler = "y" * (MIN_TOKEN_BUDGET * CHARS_PER_TOKEN * 2)

    def assemble(truncation: dict[str, object]) -> dict[str, object]:
        return {"filler": payload_filler, "truncation": truncation}

    def minimal(truncation: dict[str, object]) -> dict[str, object]:
        return {"seeds": ["seed-1"], "truncation": truncation}

    text = enforce_budget(assemble, [], minimal, token_budget=MIN_TOKEN_BUDGET)
    assert len(text) <= MIN_TOKEN_BUDGET * CHARS_PER_TOKEN
    assert '"seeds":["seed-1"]' in text
    assert '"reason":"hard-cap"' in text


def test_enforce_budget_is_deterministic() -> None:
    first_items, first_steps = _make_state_and_steps(50)
    second_items, second_steps = _make_state_and_steps(50)

    def make_assemble(
        items: list[str],
    ) -> Callable[[dict[str, object]], dict[str, object]]:
        def assemble(truncation: dict[str, object]) -> dict[str, object]:
            return {"items": list(items), "truncation": truncation}

        return assemble

    def minimal(truncation: dict[str, object]) -> dict[str, object]:
        return {"truncation": truncation}

    first = enforce_budget(
        make_assemble(first_items), first_steps, minimal, token_budget=MIN_TOKEN_BUDGET
    )
    second = enforce_budget(
        make_assemble(second_items), second_steps, minimal, token_budget=MIN_TOKEN_BUDGET
    )
    assert first == second
