"""주제 사전이 `needs.aspect_lexicon` 의 활성 버전에서 온다는 것과, 그렇게 온 사전이 상수판과
**같은 사전**이라는 것 (#8).

동등성이 이 파일의 본론이다. `contracts/interfaces.md` 의 검색 실측 표(mode x engine 여섯 줄)는
2026-08-26 에 코디네이터가 재측정해 확정한 값이고, 그 숫자는 `gold_from_chunks` -> `match_topics` 가
만든 정답 위에 서 있다. 주제 집합이 한 별칭이라도 달라지면 그 표는 조용히 낡은 표가 된다.
"""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy.engine import make_url

from analysis.retrieval import bm25, topics
from analysis.retrieval import eval as retrieval_eval
from cosmai.cli import main
from tests.retrieval import frozen_topics
from tests.retrieval.conftest import csv_topics, install_topics
from tests.retrieval.test_lexicon_v3 import expected_entries

# 상수판과 맞대는 텍스트. 별칭 전부 + 슬라이스 demo() 가 붙들던 경계(coupang 오탐·조사·활용형)다.
CORPUS = [
    "",
    "백탁없이 촉촉하게 발려요",
    "SPF50+ PA++++ 제품입니다",
    "구매링크 https://link.coupang.com/abc",
    "징크 베이스 무기자차 제품",
    "산화아연 20% 함유",
    "재구매 의사 있어요",
    "눈 시림이 심하고 눈따가워요",
    "톤 업 되는 메이크업베이스",
    "UVA UVB 둘 다 막아줍니다",
    "avobenzone 들어간 케미컬 선크림",
    "지속적으로 쓰고 있어요",
    "땀에 강하고 워터프루프",
]
CORPUS += [alias for entry in frozen_topics.TOPICS for alias in entry["ko"] + entry["latin"]]


def _connect(url: str) -> psycopg.Connection:
    parsed = make_url(url)
    return psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.database,
        options=parsed.query["options"],  # pyright: ignore[reportArgumentType]
    )


@pytest.fixture
def conn(needs_schema: str, needs_runtime_url: str):
    connection = _connect(needs_runtime_url)
    try:
        yield connection
    finally:
        connection.close()


def _same_dictionary(loaded: topics.Topics) -> None:
    """맞대는 상대는 얼어붙은 v1 **+ 포크 #56 의 판정 원장**이다. v1 그대로가 아닌 이유는 #56 이 별칭
    일곱을 더했기 때문이고, 더한 것이 정확히 그 원장뿐이라는 것은 `test_lexicon_v3.py` 가 진다 --
    여기서 상수판을 통째로 갈면 "무엇이 언제 늘었나"를 아무도 못 읽는다."""
    assert [e["topic"] for e in loaded.entries] == [e["topic"] for e in frozen_topics.TOPICS]
    for got, frozen in zip(loaded.entries, expected_entries(), strict=True):
        assert got["ko"] == frozen["ko"], got["topic"]
        assert got["latin"] == frozen["latin"], got["topic"]
        assert got["topic_type"] == frozen["topic_type"], got["topic"]
        assert got["trend_use"] == frozen["trend_use"], got["topic"]
        assert got["note"] == frozen["note"], got["topic"]
        # mfds_inci 는 집합으로 맞댄다: 한 주제 안에서 같은 말이 ko 와 mfds_inci 에 둘 다 있어
        # (아보벤존·옥토크릴렌·자외선차단제) 행 하나가 두 계열을 겸하고, 그 행의 자리는 ko 순서다.
        # 이 열은 매칭에도 질의에도 쓰이지 않으므로 순서에 뜻이 없다.
        assert set(got["mfds_inci"]) == set(frozen["mfds_inci"]), got["topic"]


def test_the_repo_csv_is_the_frozen_dictionary_plus_the_ledger():
    """`dict/topics_v1.csv` 는 적재 원본이다 -- 여기가 상수판 + 원장과 갈리면 DB 에 들어가는 사전이
    실측 표를 만든 사전도, 그 표가 어떤 델타 위에 있는지 말할 수 있는 사전도 아니게 된다."""
    _same_dictionary(csv_topics())


def test_matching_agrees_with_the_frozen_constant():
    for text in CORPUS:
        for excluded in (False, True):
            assert topics.match_topics(text, include_excluded=excluded) == frozen_topics.match_topics(
                text, include_excluded=excluded
            ), text


def test_the_queries_and_the_expansion_words_are_the_ones_the_constant_gave():
    """평가 질의와 토큰 확장 목록이 사전에서 파생된다 -- 셋 중 하나만 어긋나도 실측 표의 질의 수
    (literal 61 · heldout 60)가 달라진다."""
    frozen_queries = {
        mode: [
            (entry["topic"], alias)
            for entry in expected_entries()
            if entry["trend_use"]
            for alias in entry["ko"] + entry["latin"]
            if not (mode == "heldout" and len(entry["ko"] + entry["latin"]) < 2)
        ]
        for mode in ("literal", "heldout")
    }
    assert retrieval_eval.queries("literal") == frozen_queries["literal"]
    assert retrieval_eval.queries("heldout") == frozen_queries["heldout"]
    assert len(frozen_queries["literal"]) == 63  # v1 의 61 + #56 이 판정 주제에 더한 둘
    assert bm25.expand_words() == sorted(
        {a for e in expected_entries() for a in e["ko"] if " " not in a and len(a) >= 2}
    )


def test_the_fingerprint_follows_the_aliases():
    """색인 캐시 서명이 무는 값이다 -- 별칭이 바뀌어도 안 움직이면 옛 색인이 그대로 재사용된다."""
    before = csv_topics().fingerprint
    changed = topics.from_rows(
        [("백탁", "허옇", {"term_kind": "ko", "topic_type": "attribute", "trend_use": "true"})], 1
    )
    assert changed.fingerprint != before
    assert csv_topics().fingerprint == before  # 같은 내용은 같은 서명 (캐시 키가 결정적이어야 한다)


def test_a_row_without_a_kind_is_refused():
    # 조용히 ko 로 치면 식약처 성분명이 부분문자열 매칭에 끼어들어 매칭이 넓어진다.
    with pytest.raises(ValueError, match="term_kind"):
        topics.from_rows([("백탁", "백탁", {"topic_type": "attribute", "trend_use": "true"})], 1)


def test_a_topic_that_says_two_types_is_refused():
    rows = [
        ("백탁", "백탁", {"term_kind": "ko", "topic_type": "attribute", "trend_use": "true"}),
        ("백탁", "하얗게", {"term_kind": "ko", "topic_type": "formula"}),
    ]
    with pytest.raises(ValueError, match="백탁"):
        topics.from_rows(rows, 1)


def test_a_topic_with_no_trend_use_is_refused():
    # 기본값을 정해 두면 평가 질의에서 빠져야 할 주제가 조용히 들어온다(선크림 481/518).
    with pytest.raises(ValueError, match="trend_use"):
        topics.from_rows([("백탁", "백탁", {"term_kind": "ko", "topic_type": "attribute"})], 1)


@pytest.mark.postgres
def test_the_active_version_is_what_the_lexicon_cli_loaded(conn, needs_runtime_url: str):
    """적재 경로는 `cosmai lexicon load` 하나다 -- 검색이 자기 적재기를 따로 가지면 사전 변경이
    다시 버전을 못 받는다."""
    argv = ["lexicon", "load", "--kind", "aspect", "--version", "1"]
    assert main([*argv, str(topics.DICTIONARY_CSV), "--url", needs_runtime_url]) == 0
    activate = ["lexicon", "activate", "--kind", "aspect", "--version", "1", "--url", needs_runtime_url]
    assert main(activate) == 0
    loaded = topics.load(conn)
    assert loaded.version == 1
    _same_dictionary(loaded)


@pytest.mark.postgres
def test_a_loaded_version_does_not_move_the_dictionary_until_it_is_activated(conn, needs_runtime_url: str):
    from db.lexicon import insert_aspects
    from tests.retrieval.conftest import csv_rows

    install_topics(conn)
    before = topics.load(conn)
    with conn.cursor() as cur:
        more = ("백탁", "generic", "", "허옇", False, topics.RULESET, 1, {"term_kind": "ko"})
        wider = [*csv_rows(), more]
        insert_aspects(cur, wider, 2, active=False)
    conn.commit()
    assert topics.load(conn).fingerprint == before.fingerprint
    activate = ["lexicon", "activate", "--kind", "aspect", "--version", "2", "--url", needs_runtime_url]
    assert main(activate) == 0
    after = topics.load(conn)
    assert after.version == 2
    assert "허옇" in {e["topic"]: e["ko"] for e in after.entries}["백탁"]
    assert after.fingerprint != before.fingerprint


@pytest.mark.postgres
def test_a_schema_with_no_active_topic_rows_refuses_instead_of_matching_nothing(conn):
    """빈 사전은 오류 없이 정답 0건·질의 0개를 만든다 -- 그 초록이 "검색이 아무것도 못 찾는다"와
    구분되지 않는다. 어디를 고쳐야 하는지까지 말하고 멈춘다."""
    with pytest.raises(LookupError, match="cosmai lexicon"):
        topics.load(conn)


@pytest.mark.postgres
def test_the_polarity_ruleset_is_not_read_as_a_topic(conn):
    """aspect 사전 한 버전에는 룰셋이 여럿 산다 -- 극성 사전의 정규식이 주제 별칭으로 읽히면
    `match_topics` 가 아무 문장에나 걸린다."""
    from db.lexicon import insert_aspects

    install_topics(conn)
    with conn.cursor() as cur:
        insert_aspects(cur, [("효과없음", "generic", "", "효과|도움", True, "p1-v2.2", 1, {})], 1)
    conn.commit()
    assert [e["topic"] for e in topics.load(conn).entries] == [e["topic"] for e in frozen_topics.TOPICS]
