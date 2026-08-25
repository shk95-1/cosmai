"""5컬럼 청크 계약. check_rows 가 계약의 실행 가능한 형태이고, 이 파일이 그 검증기를 검증한다.
slices/ydc/chunks.py 의 demo() 단언을 옮긴 것 -- 위반 11종을 전부 세운다."""

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
    # 절반 앞에서 자르면 조각이 잘게 부서진다 -- 그 경우는 그냥 limit 에서 끊는다.
    pieces = split_text("가. " + "나" * 200, limit=100)
    assert len(pieces[0]) == 100


def test_clean_rows_have_no_problems():
    problems, per_source, lengths, docs = check_rows([_row(), _row(chunk_id="s:1#1", ordinal=1)])
    assert problems == []
    assert per_source == {"s": 2}
    assert lengths == [2, 2]
    assert docs == 1


def test_a_text_over_target_but_under_the_hard_stop_is_not_a_problem():
    # 500 은 목표치, 1000 은 하드스톱 -- 그 사이는 problems 가 아니라 lengths 로만 드러난다(#2/M11).
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
    # 같은 종류가 수만 건 나올 수 있다. 보고서가 그걸 다 담으면 읽을 수 없다.
    rows = [_row(chunk_id=f"s:{i}#0", doc_id=f"s:{i}", source="") for i in range(10)]
    problems, *_ = check_rows(rows)
    assert sum(1 for p in problems if p.startswith("source 없음")) == SAMPLES_PER_KIND


def test_the_kind_the_cap_counts_is_the_text_before_the_colon():
    """상한이 세는 단위를 부르는 쪽도 세어야 한다(pipeline.run 은 실행 전체에 같은 상한을 건다) --
    두 곳이 다른 규칙이면 한쪽만 걸린다(#18 M12)."""
    rows = [_row(chunk_id=f"s:{i}#0", doc_id=f"s:{i}", source="") for i in range(10)]
    problems, *_ = check_rows(rows)
    assert {problem_kind(p) for p in problems} == {"source 없음"}


def test_check_rows_accepts_a_generator():
    # 30만 행을 리스트로 물리면 메모리에 두 벌이 된다.
    problems, per_source, _, docs = check_rows(_row() for _ in range(1))
    assert problems == [] and per_source == {"s": 1} and docs == 1
