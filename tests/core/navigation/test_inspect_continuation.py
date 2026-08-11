"""Bounded source continuation: non-overlapping windows, honest rejection, budgets."""

import json
from pathlib import Path
from typing import Any

from synapse.core.index import SymbolIndex, hash_source, symbol_handle
from synapse.core.models import Symbol
from synapse.core.navigation import InspectRequest, inspect_symbols
from synapse.core.navigation.budget import CHARS_PER_TOKEN
from synapse.core.navigation.continuation import (
    continuation_token,
    parse_continuation,
    source_fingerprint,
)
from tests.core.navigation.builders import add_file, build_index, make_symbol

LONG_ID = "py:long"
LONG_FILE = "app/long.py"
LONG_START = 5
LONG_END = 124  # 120 body lines: head window plus exactly two full continuations.


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    return workspace


def _write_source(workspace: Path, file_path: str, line_count: int) -> str:
    absolute = workspace / file_path
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text("\n".join(f"line {i}" for i in range(1, line_count + 1)), encoding="utf-8")
    return hash_source(absolute.read_bytes())


def _long_symbol() -> Symbol:
    return make_symbol(LONG_ID, "process_all", LONG_FILE, line=LONG_START, end_line=LONG_END)


def _long_symbol_index(tmp_path: Path) -> tuple[SymbolIndex, Path, str]:
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    content_hash = _write_source(workspace, LONG_FILE, 130)
    add_file(index, LONG_FILE, [_long_symbol()], content_hash=content_hash)
    return index, workspace, content_hash


def _inspect(
    index: SymbolIndex,
    workspace: Path,
    symbols: tuple[str, ...],
    token_budget: int = 2400,
) -> dict[str, Any]:
    result = inspect_symbols(
        index,
        InspectRequest(symbols=symbols, token_budget=token_budget),
        workspace_root=workspace,
    )
    payload = json.loads(result)
    assert isinstance(payload, dict)
    return payload


def test_windows_walk_the_whole_symbol_without_overlap(tmp_path: Path) -> None:
    """Head slice plus continuations partition the stored span exactly once."""
    index, workspace, content_hash = _long_symbol_index(tmp_path)
    payload = _inspect(index, workspace, (symbol_handle(LONG_ID),))
    entry = payload["symbols"][0]
    src = entry["src"]
    assert src["lines"] == [LONG_START, LONG_START + 39]
    assert src["truncated"] is True
    token = src["next"]
    assert isinstance(token, str)
    # Emission is deterministic and reproducible from index state alone.
    assert token == continuation_token(
        symbol_handle(LONG_ID),
        LONG_START + 40,
        source_fingerprint(_long_symbol(), LONG_START + 40, content_hash),
    )

    windows: list[tuple[int, int, str]] = [(src["lines"][0], src["lines"][1], src["text"])]
    last_end = src["lines"][1]
    for _ in range(5):
        # Every issued token starts exactly after the last returned line.
        parsed = parse_continuation(token)
        assert parsed is not None
        assert parsed.start_line == last_end + 1
        follow_up = _inspect(index, workspace, (token,))
        continuation = follow_up["continuations"][0]
        assert continuation["h"] == symbol_handle(LONG_ID)
        windows.append((continuation["lines"][0], continuation["lines"][1], continuation["text"]))
        last_end = continuation["lines"][1]
        if not continuation["more"]:
            assert "next" not in continuation
            break
        token = continuation["next"]
    else:
        raise AssertionError("continuation walk did not terminate")

    covered: list[int] = []
    for start, end, text in windows:
        lines = text.splitlines()
        assert len(lines) == end - start + 1
        assert lines[0] == f"line {start}"
        assert lines[-1] == f"line {end}"
        covered.extend(range(start, end + 1))
    # No window repeats a line of any earlier window, and none is skipped.
    assert covered == sorted(set(covered))
    assert covered == list(range(LONG_START, LONG_END + 1))
    # Exact end-of-symbol: the final window stops at the stored span, not the file end.
    assert windows[-1][1] == LONG_END


def test_continuation_only_call_returns_the_whole_remaining_body(tmp_path: Path) -> None:
    """A continuation-only request spends its budget on source: 80 lines, one call."""
    index, workspace, _ = _long_symbol_index(tmp_path)
    payload = _inspect(index, workspace, (symbol_handle(LONG_ID),))
    token = payload["symbols"][0]["src"]["next"]

    follow_up = _inspect(index, workspace, (token, token))
    continuations = follow_up["continuations"]
    assert len(continuations) == 1  # duplicate tokens collapse deterministically
    continuation = continuations[0]
    assert continuation["lines"] == [LONG_START + 40, LONG_END]
    assert continuation["more"] is False
    assert "next" not in continuation
    head_text = payload["symbols"][0]["src"]["text"]
    assert not set(head_text.splitlines()) & set(continuation["text"].splitlines())
    coverage = follow_up["coverage"]
    assert coverage["continuation_requested"] == 1


def test_exact_fit_final_window_reports_no_more(tmp_path: Path) -> None:
    """A window ending exactly on end_line is complete: more=false, no next."""
    index, workspace, content_hash = _long_symbol_index(tmp_path)
    start = LONG_END - 39
    token = continuation_token(
        symbol_handle(LONG_ID), start, source_fingerprint(_long_symbol(), start, content_hash)
    )
    payload = _inspect(index, workspace, (token,))
    continuation = payload["continuations"][0]
    assert continuation["lines"] == [start, LONG_END]
    assert continuation["more"] is False
    assert "next" not in continuation


def test_tampering_with_the_line_component_invalidates_the_token(tmp_path: Path) -> None:
    """The fingerprint binds the exact start line: only server-issued positions work."""
    index, workspace, _ = _long_symbol_index(tmp_path)
    payload = _inspect(index, workspace, (symbol_handle(LONG_ID),))
    issued = payload["symbols"][0]["src"]["next"]
    issued_start = LONG_START + 40
    assert f"@{issued_start}:" in issued
    tampered = issued.replace(f"@{issued_start}:", f"@{issued_start + 1}:")
    assert parse_continuation(tampered) is not None  # well-formed, in range, wrong line

    follow_up = _inspect(index, workspace, (tampered,))

    assert "continuations" not in follow_up
    assert follow_up["continuation_rejected"] == [{"token": tampered, "reason": "stale"}]


def test_invalid_stale_and_out_of_range_tokens_are_rejected_with_reasons(
    tmp_path: Path,
) -> None:
    index, workspace, content_hash = _long_symbol_index(tmp_path)
    handle = symbol_handle(LONG_ID)
    malformed = "c_not-a-real-token@here"
    unknown = continuation_token(
        "s_" + "A" * 22, 45, source_fingerprint(_long_symbol(), 45, content_hash)
    )
    stale = continuation_token(
        handle, 45, source_fingerprint(_long_symbol(), 45, "hash-after-reindex")
    )
    at_head = continuation_token(
        handle, LONG_START, source_fingerprint(_long_symbol(), LONG_START, content_hash)
    )
    beyond_end = continuation_token(
        handle, LONG_END + 1, source_fingerprint(_long_symbol(), LONG_END + 1, content_hash)
    )

    payload = _inspect(index, workspace, (malformed, unknown, stale, at_head, beyond_end))

    rejected = {row["token"]: row["reason"] for row in payload["continuation_rejected"]}
    assert rejected == {
        malformed: "invalid",
        unknown: "unknown-symbol",
        stale: "stale",
        at_head: "out-of-range",
        beyond_end: "out-of-range",
    }
    assert "continuations" not in payload
    assert payload["coverage"]["continuation_requested"] == 5
    # Rejected continuations never leak into the unknown-symbol channel.
    assert "missing" not in payload


def test_edited_file_is_rejected_never_spliced(tmp_path: Path) -> None:
    """A disk edit between calls can never mix two file versions in one walk."""
    index, workspace, _ = _long_symbol_index(tmp_path)
    payload = _inspect(index, workspace, (symbol_handle(LONG_ID),))
    token = payload["symbols"][0]["src"]["next"]

    # Edit the file on disk without re-indexing: same length, different content.
    absolute = workspace / LONG_FILE
    absolute.write_text(
        "\n".join(f"edited {i}" for i in range(1, 131)),
        encoding="utf-8",
    )

    follow_up = _inspect(index, workspace, (token,))

    assert "continuations" not in follow_up
    assert follow_up["continuation_rejected"] == [{"token": token, "reason": "stale"}]
    # Nothing anywhere in the payload carries post-edit content.
    assert "edited" not in json.dumps(follow_up)


def test_missing_source_file_rejects_the_token_honestly(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    add_file(index, LONG_FILE, [_long_symbol()])  # indexed, but never written to disk

    stored_hash = f"hash-{LONG_FILE}"  # builders' placeholder for the stored row
    token = continuation_token(
        symbol_handle(LONG_ID), 45, source_fingerprint(_long_symbol(), 45, stored_hash)
    )
    payload = _inspect(index, workspace, (token,))

    rejected = payload["continuation_rejected"]
    assert rejected == [{"token": token, "reason": "source-unavailable"}]


WIDE_ID = "py:wide"
WIDE_FILE = "app/wide.py"
WIDE_START = 5
WIDE_END = 300  # 296 body lines, each ~200 chars: one window can never fit whole


def _write_wide_source(workspace: Path, file_path: str, line_count: int, width: int) -> str:
    absolute = workspace / file_path
    absolute.parent.mkdir(parents=True, exist_ok=True)
    filler = "x" * width
    absolute.write_text(
        "\n".join(f"{filler} line {i}" for i in range(1, line_count + 1)), encoding="utf-8"
    )
    return hash_source(absolute.read_bytes())


def _wide_symbol() -> Symbol:
    return make_symbol(WIDE_ID, "render_all", WIDE_FILE, line=WIDE_START, end_line=WIDE_END)


def _wide_symbol_index(tmp_path: Path, width: int = 190) -> tuple[SymbolIndex, Path, str]:
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    content_hash = _write_wide_source(workspace, WIDE_FILE, 320, width)
    add_file(index, WIDE_FILE, [_wide_symbol()], content_hash=content_hash)
    return index, workspace, content_hash


def _wide_token(content_hash: str, start: int = WIDE_START + 40) -> str:
    return continuation_token(
        symbol_handle(WIDE_ID), start, source_fingerprint(_wide_symbol(), start, content_hash)
    )


def test_long_lines_force_halving_and_next_tracks_the_returned_end(tmp_path: Path) -> None:
    """~200-char lines cannot fit 256 at the default budget: halve, then chain cleanly."""
    index, workspace, content_hash = _wide_symbol_index(tmp_path)
    token = _wide_token(content_hash)
    result = inspect_symbols(index, InspectRequest(symbols=(token,)), workspace_root=workspace)
    assert len(result) <= 2400 * CHARS_PER_TOKEN
    payload = json.loads(result)
    continuation = payload["continuations"][0]
    start, end = continuation["lines"]
    returned = end - start + 1
    assert start == WIDE_START + 40
    assert returned < 256  # halved at least once
    assert returned in (128, 64, 32, 16, 10)  # only deterministic halving sizes
    assert continuation["more"] is True
    assert payload["budget"]["dropped"]["continuation"] >= 1
    follow = parse_continuation(continuation["next"])
    assert follow is not None
    assert follow.start_line == end + 1  # next tracks the returned end, not the request

    second = _inspect(index, workspace, (continuation["next"],))
    second_start = second["continuations"][0]["lines"][0]
    assert second_start == end + 1  # no overlap, no gap after shrinking


def test_continuation_only_payload_can_approach_the_hard_cap(tmp_path: Path) -> None:
    """With ~33-char lines the full 256-line window fits just under 9,600 chars."""
    index, workspace, content_hash = _wide_symbol_index(tmp_path, width=25)
    token = _wide_token(content_hash)
    result = inspect_symbols(index, InspectRequest(symbols=(token,)), workspace_root=workspace)
    assert len(result) <= 2400 * CHARS_PER_TOKEN
    assert len(result) > 8000  # most of the budget went to source
    payload = json.loads(result)
    continuation = payload["continuations"][0]
    # 45..300 is exactly 256 lines: the full window, terminating on the stored end.
    assert continuation["lines"] == [WIDE_START + 40, WIDE_END]
    assert continuation["more"] is False
    assert "next" not in continuation
    assert payload["budget"]["complete"] is True


def test_mixed_token_and_handles_shortens_and_solo_resend_maximizes(tmp_path: Path) -> None:
    """Mixing shares the budget: the window shrinks but stays honest and chainable."""
    index, workspace, content_hash = _wide_symbol_index(tmp_path)
    others = []
    for i in range(3):
        other_id = f"py:other-{i}"
        other_file = f"app/other_{i}.py"
        others.append(other_id)
        other_hash = _write_source(workspace, other_file, 60)
        add_file(
            index,
            other_file,
            [make_symbol(other_id, f"worker_{i}", other_file, line=1, end_line=60)],
            content_hash=other_hash,
        )
    token = _wide_token(content_hash)

    mixed = _inspect(index, workspace, (token, *map(symbol_handle, others)))
    solo = _inspect(index, workspace, (token,))

    assert mixed["coverage"]["continuation_requested"] == 1
    mixed_conts = mixed.get("continuations")
    solo_lines = solo["continuations"][0]["lines"]
    solo_size = solo_lines[1] - solo_lines[0] + 1
    if mixed_conts:
        start, end = mixed_conts[0]["lines"]
        assert end - start + 1 <= solo_size  # never larger than the solo window
        assert end - start + 1 < 256
    else:
        assert mixed["coverage"]["continuation_omitted"] == 1
    # The token survives shortening/omission unchanged and keeps working alone.
    assert solo_lines[0] == WIDE_START + 40
    assert solo["continuations"][0]["more"] is True


def test_tight_budget_omits_the_continuation_honestly(tmp_path: Path) -> None:
    """At the minimum budget a wide continuation cannot fit at all: omit and report."""
    index, workspace, content_hash = _wide_symbol_index(tmp_path)
    others = []
    for i in range(2):
        other_id = f"py:tiny-{i}"
        other_file = f"app/tiny_{i}.py"
        others.append(other_id)
        other_hash = _write_source(workspace, other_file, 60)
        add_file(
            index,
            other_file,
            [make_symbol(other_id, f"job_{i}", other_file, line=1, end_line=60)],
            content_hash=other_hash,
        )
    token = _wide_token(content_hash)
    token_budget = 500
    result = inspect_symbols(
        index,
        InspectRequest(
            symbols=(symbol_handle(others[0]), symbol_handle(others[1]), token),
            token_budget=token_budget,
        ),
        workspace_root=workspace,
    )
    assert len(result) <= token_budget * CHARS_PER_TOKEN
    payload = json.loads(result)
    coverage = payload["coverage"]
    assert coverage["continuation_requested"] == 1
    continuations = payload.get("continuations")
    if continuations:
        start, end = continuations[0]["lines"]
        assert end - start + 1 <= 16  # at or near the floor
    else:
        assert coverage["continuation_omitted"] == 1
    assert payload["budget"]["complete"] is False


def test_remaining_217_lines_return_in_one_continuation_only_call(tmp_path: Path) -> None:
    """The T3 shape: head plus a 217-line tail must cost exactly one follow-up."""
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    tall_id, tall_file = "py:tall", "app/tall.py"
    tall = make_symbol(tall_id, "run_everything", tall_file, line=5, end_line=261)
    content_hash = _write_source(workspace, tall_file, 270)
    add_file(index, tall_file, [tall], content_hash=content_hash)

    payload = _inspect(index, workspace, (symbol_handle(tall_id),))
    src = payload["symbols"][0]["src"]
    assert src["lines"] == [5, 44]  # normal head stays capped at 40 lines
    follow_up = _inspect(index, workspace, (src["next"],))
    continuation = follow_up["continuations"][0]
    assert continuation["lines"] == [45, 261]  # all 217 remaining lines at once
    assert continuation["more"] is False
    assert "next" not in continuation


def test_body_beyond_the_ceiling_needs_exactly_two_windows(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    index = build_index(tmp_path)
    huge_id, huge_file = "py:huge", "app/huge.py"
    huge = make_symbol(huge_id, "run_forever", huge_file, line=5, end_line=340)
    content_hash = _write_source(workspace, huge_file, 350)
    add_file(index, huge_file, [huge], content_hash=content_hash)

    payload = _inspect(index, workspace, (symbol_handle(huge_id),))
    token = payload["symbols"][0]["src"]["next"]
    first = _inspect(index, workspace, (token,))["continuations"][0]
    assert first["lines"] == [45, 45 + 255]  # full 256-line window
    assert first["more"] is True
    second = _inspect(index, workspace, (first["next"],))["continuations"][0]
    assert second["lines"] == [301, 340]
    assert second["more"] is False


def test_halving_walks_the_full_ladder_to_the_floor() -> None:
    from synapse.core.index import SourceSlice
    from synapse.core.navigation.inspection import (
        CONTINUATION_FLOOR_LINES,
        MAX_CONTINUATION_LINES,
        _Continuation,
        _continuation_halving_steps,
        _halve_continuation,
    )

    item = _Continuation(
        token="c_x",
        symbol=_long_symbol(),
        handle=symbol_handle(LONG_ID),
        start_line=45,
        src=SourceSlice(start_line=45, end_line=124, text="", truncated=False),
        content_hash="h",
    )
    sizes = [item.src_max]
    while _halve_continuation(item):
        sizes.append(item.src_max)
    assert sizes == [256, 128, 64, 32, 16, 10]
    assert sizes[0] == MAX_CONTINUATION_LINES
    assert sizes[-1] == CONTINUATION_FLOOR_LINES
    # The drop-step generator provisions exactly this many halvings.
    assert _continuation_halving_steps() == len(sizes) - 1


def test_next_token_is_correct_at_every_halving_size(tmp_path: Path) -> None:
    from synapse.core.index import SourceSlice
    from synapse.core.navigation.inspection import _Continuation, _continuation_entry
    from synapse.core.navigation.render import FileTable

    tall = make_symbol("py:unit-tall", "unit_tall", "app/unit.py", line=5, end_line=344)
    body_lines = [f"line {i}" for i in range(45, 45 + 300)]
    src = SourceSlice(start_line=45, end_line=45 + 299, text="\n".join(body_lines), truncated=True)
    for size in (256, 128, 64, 32, 16, 10):
        item = _Continuation(
            token="c_x",
            symbol=tall,
            handle=symbol_handle(tall.id),
            start_line=45,
            src=src,
            content_hash="h",
            src_max=size,
        )
        entry = _continuation_entry(item, FileTable())
        lines = entry["lines"]
        assert isinstance(lines, list)
        assert lines == [45, 45 + size - 1]
        assert entry["more"] is True
        token = entry["next"]
        assert isinstance(token, str)
        parsed = parse_continuation(token)
        assert parsed is not None
        assert parsed.start_line == lines[1] + 1


def test_payload_without_tokens_is_unchanged(tmp_path: Path) -> None:
    """No continuation was requested, so no continuation key may appear anywhere."""
    index, workspace, _ = _long_symbol_index(tmp_path)
    payload = _inspect(index, workspace, (symbol_handle(LONG_ID),))
    assert "continuations" not in payload
    assert "continuation_rejected" not in payload
    assert "continuation_requested" not in payload["coverage"]
    # The only addition for an incomplete source is the deterministic next token.
    src = payload["symbols"][0]["src"]
    assert set(src) == {"lines", "truncated", "text", "next"}
    again = _inspect(index, workspace, (symbol_handle(LONG_ID),))
    assert again == payload  # same index state, same token: replayable
