"""The five-column chunk contract. check_rows is the runnable form of the contract, and this file validates
that validator. Carried over from the demo() assertions of slices/ydc/chunks.py -- all 11 violation kinds."""

from __future__ import annotations

import pytest

from analysis.retrieval.chunks import (
    FIELDS,
    MAX_CHARS,
    SAMPLES_PER_KIND,
    check_rows,
    problem_kind,
    split_text,
)


def _row(**over):
    row = {"chunk_id": "s:1#0", "doc_id": "s:1", "source": "s", "ordinal": 0, "text": "백탁"}
    row.update(over)
    return row


def test_the_contract_is_five_columns():
    assert FIELDS == ("chunk_id", "doc_id", "source", "ordinal", "text")


def test_short_text_is_one_piece_and_empty_text_is_none():
    assert split_text("짧다") == ["짧다"]
    assert split_text("") == []
    assert split_text("   ") == []


def test_long_text_is_split_under_the_limit():
    pieces = split_text("가" * 1200, limit=100)
    assert len(pieces) > 1
    assert all(len(p) <= 100 for p in pieces)
    assert "".join(pieces) == "가" * 1200


def test_a_split_prefers_a_sentence_end_over_the_hard_limit():
    text = "가" * 60 + ". " + "나" * 60
    pieces = split_text(text, limit=100)
    assert pieces[0] == "가" * 60 + "."


def test_a_break_too_early_in_the_window_is_ignored():
    # Cutting before the halfway mark shatters the piece -- in that case it is simply cut at limit.
    pieces = split_text("가. " + "나" * 200, limit=100)
    assert len(pieces[0]) == 100


def test_clean_rows_have_no_problems():
    problems, per_source, lengths, docs = check_rows([_row(), _row(chunk_id="s:1#1", ordinal=1)])
    assert problems == []
    assert per_source == {"s": 2}
    assert lengths == [2, 2]
    assert docs == 1


def test_a_text_over_target_but_under_the_hard_stop_is_not_a_problem():
    # 500 is the target and 1000 the hard stop -- in between it shows only in lengths, not in problems
    # (#2/M11).
    problems, _per_source, lengths, _docs = check_rows([_row(text="가" * 600)])
    assert problems == []
    assert lengths == [600]


@pytest.mark.parametrize(
    ("over", "expected"),
    [
        ({"chunk_id": ""}, "chunk_id 없음"),
        ({"doc_id": ""}, "doc_id 없음"),
        ({"source": ""}, "source 없음"),
        ({"text": "  "}, "text 비어 있음"),
        ({"text": "백  탁"}, "정규화 안 됨"),
        ({"text": "가" * (MAX_CHARS * 2 + 1)}, "너무 긺"),
        ({"ordinal": "x"}, "ordinal 이 정수가 아님"),
        ({"ordinal": 3}, "ordinal 이 0 부터 연속이 아님"),
    ],
)
def test_each_violation_is_reported(over, expected):
    problems, *_ = check_rows([_row(**over)])
    assert any(p.startswith(expected) for p in problems), problems


def test_a_duplicate_chunk_id_is_reported():
    problems, *_ = check_rows([_row(), _row(ordinal=1)])
    assert any(p.startswith("chunk_id 중복") for p in problems), problems


def test_violations_of_one_kind_are_capped_at_three():
    # One kind can come up tens of thousands of times. A report holding all of it cannot be read.
    rows = [_row(chunk_id=f"s:{i}#0", doc_id=f"s:{i}", source="") for i in range(10)]
    problems, *_ = check_rows(rows)
    assert sum(1 for p in problems if p.startswith("source 없음")) == SAMPLES_PER_KIND


def test_the_kind_the_cap_counts_is_the_text_before_the_colon():
    """The caller has to count the unit the cap counts as well (pipeline.run puts the same cap on the whole
    run) -- with different rules in the two places only one of them applies (#18 M12)."""
    rows = [_row(chunk_id=f"s:{i}#0", doc_id=f"s:{i}", source="") for i in range(10)]
    problems, *_ = check_rows(rows)
    assert {problem_kind(p) for p in problems} == {"source 없음"}


def test_check_rows_accepts_a_generator():
    # Holding 300k rows as a list keeps two copies in memory.
    problems, per_source, _, docs = check_rows(_row() for _ in range(1))
    assert problems == [] and per_source == {"s": 1} and docs == 1


def test_a_violation_points_with_the_chunk_id_not_a_row_number():
    """The coordinate is the table's primary key -- a person goes to the original with one
    `WHERE chunk_id = ...`. "Which row" is the ordinal of this call's scan rather than a CSV line, so across
    a whole run it pointed at several documents (#27)."""
    problems, *_ = check_rows([_row(text="백  탁")])
    assert problems == ["정규화 안 됨: s:1#0"]


def test_a_row_without_a_chunk_id_is_pointed_at_by_doc_id_and_ordinal():
    # A row that cannot make a chunk_id still has the (doc_id, ordinal) index as a coordinate to reach it by.
    problems, *_ = check_rows([_row(chunk_id="", ordinal=2)])
    assert any("doc_id=s:1" in p and "ordinal=2" in p for p in problems), problems


def test_a_row_with_no_key_at_all_falls_back_to_its_place_in_the_run():
    # With every key column empty, the only coordinate left is the scan order -- something still has to be
    # left.
    problems, *_ = check_rows([_row(chunk_id="", doc_id="")])
    assert any(p.startswith("chunk_id 없음: 2행 (source=s)") for p in problems), problems
    problems, *_ = check_rows([_row(chunk_id="", doc_id="", source="")])
    assert any(p.startswith("chunk_id 없음: 2행") for p in problems), problems


def test_the_place_in_the_run_continues_across_calls():
    """pipeline.run calls check_rows per batch -- numbering from 2 every time makes one coordinate point at
    several documents (#27). The caller gives the number it has been counting on."""
    problems, _per, lengths, _docs = check_rows([_row(chunk_id="", doc_id="", source="")])
    later, *_ = check_rows([_row(chunk_id="", doc_id="", source="")], first_line=2 + len(lengths))
    assert problems != later
    assert any(p.startswith("chunk_id 없음: 3행") for p in later), later
