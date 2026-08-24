"""need_mention 의 자연키 — 긴 문장이 btree 상한을 넘지 않고, extractor_version 이 키에 있다 (005).

두 결함이 같은 수술을 요구했다. #5 운영 첫 실행은 `index row size 3336 exceeds btree version 4
maximum 2704 for index "need_mention_src_ref_need_key_sentence_key"` 로 죽었고(길이 제한 없는
`sentence` 가 btree 키에 그대로 들어갔다), 키에 버전이 없어 시드 행과 분석 행이 같은 자리를 다퉜다.
"""

from __future__ import annotations

import hashlib
from typing import Any

import psycopg
import pytest

from db.seed._common import connect

pytestmark = pytest.mark.postgres

# 3200B, 운영에서 터진 3336B 와 같은 자릿수. 반복 문자열은 btree 가 압축해 상한을 넘지 않으므로
# 엔트로피가 높아야 한다 — 문장 분할점이 없어 리뷰 하나가 통째로 한 "문장"이 된 실제 모양의 대역이다.
LONG_SENTENCE = "".join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(50))
INSERT = (
    "INSERT INTO need_mention (src, site, ref, need_key, polarity, observed_at,"
    " observed_at_resolution, month, sentence, extractor_version, polarity_version)"
    " VALUES ('review', 'oliveyoung', %s, '발림성', '불만', '2026-03-04', 'day', '2026-03', %s, %s, %s)"
)


def _insert(url: str, ref: str, sentence: str, extractor: str, polarity_version: str = "rule-v2.2") -> None:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(INSERT, (ref, sentence, extractor, polarity_version))
        conn.commit()


def _rows(url: str, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(query, params)  # type: ignore[arg-type]
        return cur.fetchall()


def test_a_sentence_past_the_btree_row_limit_is_stored(needs_runtime_url: str):
    """#5 를 멈춘 행. 문장 자체가 키에 있으면 2704B 상한에 걸려 run 전체가 실패한다."""
    assert len(LONG_SENTENCE.encode()) > 2704
    _insert(needs_runtime_url, "P1/LONG", LONG_SENTENCE, "rule-v2.2")
    assert _rows(needs_runtime_url, "SELECT count(*) FROM need_mention") == [(1,)]


def test_two_extractor_versions_of_the_same_sentence_both_survive(needs_runtime_url: str):
    """안 A: 키에 버전이 들어가므로 시드(slice-*)와 분석(rule-v*)이 같은 자리를 다투지 않는다."""
    for extractor, polarity_version in (("slice-suncare", "rule-v2.1"), ("rule-v2.2", "rule-v2.2")):
        _insert(needs_runtime_url, "P1/R1", "끈적여요", extractor, polarity_version)
    found = _rows(
        needs_runtime_url,
        "SELECT extractor_version FROM need_mention WHERE ref = 'P1/R1' ORDER BY extractor_version",
    )
    assert found == [("rule-v2.2",), ("slice-suncare",)]


def test_one_extractor_version_still_gets_one_row_per_sentence(needs_runtime_url: str):
    """키가 느슨해진 것이 아니다 — 같은 버전이 같은 문장을 다시 내면 여전히 충돌한다."""
    _insert(needs_runtime_url, "P1/R1", "끈적여요", "rule-v2.2")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert(needs_runtime_url, "P1/R1", "끈적여요", "rule-v2.2")


def test_the_natural_key_is_a_unique_index_upserts_can_name(needs_runtime_url: str):
    """ON CONFLICT 는 인덱스 표현식과 같은 형태로만 매칭된다 — 그 형태를 여기서 고정한다."""
    definition = _rows(
        needs_runtime_url,
        "SELECT indexdef FROM pg_indexes WHERE tablename = 'need_mention'"
        " AND schemaname = current_schema() AND indexdef LIKE '%%md5%%'",
    )
    assert len(definition) == 1
    assert "UNIQUE" in definition[0][0]
    assert "(src, ref, need_key, extractor_version, md5(sentence))" in definition[0][0]
