"""미포착 표현 목록 (#8).

사전은 자기가 못 잡는 말을 스스로 말하지 못한다 -- 사전 밖의 성분·제형은 검색에도 트렌드 판정에도
아예 관측되지 않고, 그 사실은 어떤 숫자로도 나타나지 않는다. 이 목록이 그 천장을 사람에게 보이는
유일한 자리라, 여기서 붙드는 것은 "무엇이 후보에서 빠지는가"다: 사전에 이미 걸린 말과 대조군에도
흔한 일반어.
"""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy.engine import make_url

from analysis.retrieval import terms
from tests.retrieval.conftest import install_topics

pytestmark = pytest.mark.postgres

# 주제가 걸린 문서 6건에만 나오는 말(병원)과, 어디에나 나오는 말(사람)을 같이 넣는다.
TOPICAL = [f"백탁이 심해서 병원 다녀왔다 사람들 조심하세요 {i}" for i in range(6)]
CONTROL = [f"사람들이 많이 사는 물건이다 {i}" for i in range(6)]
INGREDIENT = ["에칠헥실트리아존 들어간 제품", "티타늄디옥사이드 함유", "구매링크 https://link.coupang.com/x"]


@pytest.fixture
def corpus(needs_schema: str, needs_runtime_url: str):
    parsed = make_url(needs_runtime_url)
    conn = psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.database,
        options=parsed.query["options"],  # pyright: ignore[reportArgumentType]
    )
    install_topics(conn)
    with conn.cursor() as cur:
        for i, text in enumerate([*TOPICAL, *CONTROL, *INGREDIENT]):
            doc = f"d{i:03d}"
            cur.execute(
                "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
                "VALUES (%s, %s, 'youtube_comment', 0, %s, 'x')",
                (f"{doc}#0", doc, text),
            )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def test_a_noun_the_dictionary_already_catches_is_not_a_candidate(corpus):
    # 사전에 있는 말이 후보에 남으면 목록이 "천장"이 아니라 사전 사본이 된다.
    found = {row.term for row in terms.unmatched(terms.scan(corpus))}
    assert "백탁" not in found
    assert "병원" in found


def test_a_word_that_is_just_as_common_outside_the_topics_is_not_a_candidate(corpus):
    """빈도만으로 뽑으면 상위가 피부·제품·사람으로 채워진다 -- 선크림이라서 많은 말이 아니라
    한국어라서 많은 말이다(ydc 실측). 대조군 대비 비중으로 거른다."""
    found = {row.term for row in terms.unmatched(terms.scan(corpus))}
    assert "사람" not in found


def test_every_dictionary_term_gets_a_row_even_when_it_never_appears(corpus):
    """등장 0건도 남긴다 -- 식약처 성분명이 유튜브에 안 나온다는 사실이 매핑이 필요하다는 근거다."""
    rows = {(row.topic, row.term): row for row in terms.ingredients(terms.scan(corpus))}
    assert rows[("유기자차", "에칠헥실트리아존")].docs == 1
    assert rows[("무기자차", "티타늄디옥사이드")].docs == 1
    assert rows[("무기자차", "산화아연")].docs == 0
    assert rows[("유기자차", "에칠헥실트리아존")].term_kind == "mfds_inci"


def test_a_latin_term_keeps_its_boundary_match(corpus):
    # 부분문자열로 세면 PA 가 coupang 에 걸려 오탐 16% 가 그대로 표에 실린다.
    rows = {(row.topic, row.term): row for row in terms.ingredients(terms.scan(corpus))}
    assert rows[("SPF_PA", "PA")].docs == 0


def test_the_report_says_how_to_put_a_term_into_the_dictionary(corpus):
    # 목록은 사람이 사전에 넣는 입력이다 -- 넣는 길이 적혀 있지 않으면 그 길을 각자 발명한다.
    rendered = terms.render(terms.scan(corpus))
    assert "cosmai lexicon" in rendered
    assert terms.DICTIONARY_CSV.name in rendered
