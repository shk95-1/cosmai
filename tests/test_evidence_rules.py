"""Are the four evidence selection rules exactly what the contract's §Evidence writes (fork #6).

DB 없이 도는 자리다 — 규칙은 후보 목록에서 근거 행을 만드는 순수 함수이고, 그래서 저장된 표에서도
ydc 의 원 수집 CSV 에서도 같은 코드가 돈다(`analysis/judge` 가 쓴 방식). 골든이 성립하는 자리가 그것이다.
"""

from __future__ import annotations

import hashlib

import pytest

from analysis.evidence import TOP_PER_CELL, Candidate, author_hash, select

CELL = {
    "run_id": 7,
    "scope": "선블록",
    "content_type": "long_form",
    "panel_version": 1,
    "panel_role": "product",
    "snapshot_id": 3,
}
COMMENT = "youtube_comment"
CHANNEL = "UCat2CSzaple02nnhbUSJ2zg"


def candidate(doc: str, *, likes: int, topic: str = "백탁", quarter: str = "2025Q2", **rest) -> Candidate:
    fields: dict = {
        "doc_id": f"{COMMENT}:{doc}",
        "quarter": quarter,
        "topic_key": topic,
        "source": COMMENT,
        "channel_id": CHANNEL,
        "like_count": likes,
        "author_channel_hash": "not-the-creator",
        "quality_flags": "",
        "matched_term": "백탁",
    }
    fields.update(rest)
    return Candidate(**fields)


def cells(*keys: tuple[str, str]) -> set[tuple[str, str, str]]:
    return {(topic, quarter, COMMENT) for topic, quarter in keys}


def picked(candidates, *, top: int = TOP_PER_CELL, known=None):
    return select(
        candidates, cells=known if known is not None else cells(("백탁", "2025Q2")), top=top, **CELL
    )


def test_the_author_hash_is_the_rule_the_collector_used():
    """이 해시가 수집기의 것과 다르면 제작자 댓글이 하나도 안 걸리고, 그 조용한 통과가 근거를 오염시킨다."""
    assert author_hash(CHANNEL) == hashlib.sha256(f"youtube:{CHANNEL}".encode()).hexdigest()[:24]
    assert len(author_hash(CHANNEL)) == 24
    assert author_hash("UCabc") != author_hash("UCxyz")


def test_the_creator_own_comment_is_not_evidence():
    """좋아요 1위여도 뺀다 — 상위가 대부분 고정 댓글(타임라인·인사말)이라 소비자 발화가 아니다."""
    rows = picked(
        [
            candidate("mine", likes=999, author_channel_hash=author_hash(CHANNEL)),
            candidate("theirs", likes=1),
        ]
    )
    assert [row.doc_id for row in rows] == [f"{COMMENT}:theirs"]


def test_a_flagged_document_is_not_quoted():
    """빈 본문과 같은 영상 안 복붙. 지표는 후자를 unique_ratio 의 분모에 세지만 인용은 다른 일이다."""
    rows = picked(
        [
            candidate("empty", likes=99, quality_flags="empty_text"),
            candidate("dupe", likes=98, quality_flags="duplicate_in_parent"),
            candidate("real", likes=1),
        ]
    )
    assert [row.doc_id for row in rows] == [f"{COMMENT}:real"]


def test_the_ladder_is_likes_descending_and_the_tie_is_broken_by_doc_id():
    """2차 키가 없으면 동점의 승자를 읽기 순서가 정하고, 그 표는 재실행이 같은 행을 내지 않는다."""
    rows = picked([candidate("b", likes=5), candidate("a", likes=5), candidate("c", likes=9)])
    assert [(row.rank, row.doc_id) for row in rows] == [
        (1, f"{COMMENT}:c"),
        (2, f"{COMMENT}:a"),
        (3, f"{COMMENT}:b"),
    ]


def test_the_ladder_starts_at_one_and_has_no_gap():
    rows = picked([candidate(str(i), likes=i) for i in range(10)])
    assert [row.rank for row in rows] == [1, 2, 3]


def test_the_cap_is_three_per_cell_and_is_the_callers_knob():
    assert TOP_PER_CELL == 3
    many = [candidate(str(i), likes=i) for i in range(10)]
    assert len(picked(many)) == 3
    assert len(picked(many, top=5)) == 5


def test_a_candidate_outside_the_judgement_grid_is_dropped():
    """FK 가 거절할 행을 만들지 않는다 — trend_use=false 인 주제(`선크림`)에는 판정 셀이 없다."""
    rows = picked(
        [candidate("a", likes=3), candidate("b", likes=9, topic="선크림")],
        known=cells(("백탁", "2025Q2")),
    )
    assert [row.topic_key for row in rows] == ["백탁"]


def test_one_comment_stands_for_every_topic_it_mentions():
    """한 문서가 여러 주제에 걸리면 그 셀들 각각의 근거다 — 후보는 (문서, 주제) 하나가 한 줄이다."""
    rows = picked(
        [candidate("a", likes=3), candidate("a", likes=3, topic="유기자차", matched_term="유기자차")],
        known=cells(("백탁", "2025Q2"), ("유기자차", "2025Q2")),
    )
    assert {(row.topic_key, row.matched_term) for row in rows} == {("백탁", "백탁"), ("유기자차", "유기자차")}


def test_the_row_carries_the_cell_and_the_reason_it_was_picked():
    (row,) = picked([candidate("a", likes=4)])
    assert (row.run_id, row.scope, row.source, row.content_type) == (7, "선블록", COMMENT, "long_form")
    assert (row.panel_version, row.panel_role, row.snapshot_id) == (1, "product", 3)
    assert (row.topic_key, row.quarter, row.like_count, row.matched_term) == ("백탁", "2025Q2", 4, "백탁")


def test_the_rows_come_out_in_a_stable_order():
    """적재가 executemany 로 그대로 나가므로, 순서가 흔들리면 같은 표를 두 번 써도 diff 가 생긴다."""
    made = [candidate(str(i), likes=i % 3, topic=t) for i in range(6) for t in ("백탁", "유기자차")]
    known = cells(("백탁", "2025Q2"), ("유기자차", "2025Q2"))
    first = picked(made, known=known)
    second = picked(list(reversed(made)), known=known)
    assert [(r.topic_key, r.quarter, r.rank, r.doc_id) for r in first] == [
        (r.topic_key, r.quarter, r.rank, r.doc_id) for r in second
    ]
    assert first == sorted(first, key=lambda r: (r.source, r.topic_key, r.quarter, r.rank))


@pytest.mark.parametrize("flags", ["", "duplicate_in_parent"])
def test_the_quality_gate_is_the_empty_string_not_a_list_of_known_flags(flags: str):
    """A different gate from `counted` — the metrics count copy-paste but the evidence does not quote it (the
    contract's §Evidence, rule 1)."""
    rows = picked([candidate("a", likes=1, quality_flags=flags)])
    assert bool(rows) == (flags == "")
