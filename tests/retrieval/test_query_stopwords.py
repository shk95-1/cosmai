"""질의 축의 불용어 (포크 #46). 색인 축은 여기서 아무것도 안 바뀐다.

두 축이 다르다는 것이 이 파일의 본론이다. `bm25.tokenize` 는 그대로 두고 `bm25.tokenize_query` 만
목록을 탄다 -- 색인에서 `소비자` 를 빼면 `소비자` 를 직접 찾는 질의를 못 하게 된다. 목록이 사는 자리는
`needs.entity_lexicon` 의 `kind='stopword'` · `canonical='query'` 활성 버전이고, aspect 사전과 버전 축이
따로 돈다(`entity_lexicon` 의 activate 는 kind 단위)는 것도 여기서 못 박는다 -- 포크 #56 이 같은 시기에
aspect v3 를 올리는데, 두 개정이 같은 버전 번호를 다투면 한쪽이 다른 쪽을 끈다.
"""

from __future__ import annotations

import csv

import psycopg
import pytest
from sqlalchemy.engine import make_url

from analysis.retrieval import bm25, stopwords
from analysis.retrieval import eval as retrieval_eval
from analysis.retrieval import topics as topics_module
from cosmai.cli import main
from tests.retrieval.conftest import csv_stopwords, install_stopwords, install_topics, stopword_rows

# ydc `v0.3.0` 의 `seeds/stopwords_ko.txt` 뒤에 붙은 질의 메타 블록 그대로(12개). 그 파일은 읽기 전용
# 원본이라 레포로 들여오지 않고 여기 시험 벡터로만 둔다 -- `test_particles.py` 의 조사 30개와 같은 자리다.
YDC_QUERY_META = (
    "소비자",
    "사람",
    "사람들",
    "반응",
    "의견",
    "얘기",
    "이야기",
    "언급",
    "대하",
    "관하",
    "뭐라",
    "어떻",
)
# 같은 파일 앞쪽(일반어 블록 30개) 중 우리 목록에 들어오는 유일한 하나. ydc 는 파일 전체를 질의에
# 적용하므로 이 말도 질의에서 빠졌고, 이슈가 든 근거 예시가 이것을 뺀 결과다.
FROM_YDC_GENERAL = ("관련",)
# 그 일반어 블록의 나머지. 우리 쪽에서는 색인 축의 판단(포크 #8 의 lift · #37)이 이미 처분했다.
YDC_INDEX_AXIS = (
    "것", "수", "등", "때", "거", "분", "번", "중", "점", "쪽", "듯",
    "영상", "댓글", "채널", "오늘", "이번", "지금", "그냥", "더", "또",
    "여러분", "안녕", "구독", "링크", "클릭", "확인", "하", "되", "있",
)  # fmt: skip

# 목록의 표기가 실제로 나오는 자연어 질의. 표기가 아니라 **토큰**을 적는 목록이라(`관해서` -> `관하`)
# 그 사실을 사람이 읽을 수 있는 형태로 붙들어 둔다.
PROBES = (
    "백탁 관련해서 소비자들이",
    "백탁에 관해서 사람들이 뭐라고 하나요",
    "선크림 발림성에 대해서 어떻게 이야기하나",
    "톤업 반응이 어떤지 사람들 의견",
    "무기자차 언급된 댓글 얘기",
)
UNREACHABLE = "도달하지 않는다"


def _rows() -> list[dict[str, str]]:
    with stopwords.DICTIONARY_CSV.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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


@pytest.fixture
def installed():
    """레포 CSV 를 이 프로세스의 활성 목록으로. DB 를 쓰는 테스트는 자기 스키마에서 다시 세운다."""
    active = stopwords.use(csv_stopwords())
    yield active
    stopwords.forget()


# ---------- 목록이 무엇이고 어디에 사는가 ----------


def test_the_repo_csv_carries_the_ydc_judgement_and_nothing_from_the_index_axis():
    """ydc `v0.3.0` 판과의 대조. 질의 메타 12 + `관련` 이고, 일반어 블록 29 는 오지 않는다 --
    그쪽은 색인 축이라 lift 와 2글자 규칙이 이미 처분했다(포크 #8·#37)."""
    surfaces = tuple(row["surface"] for row in _rows())
    assert surfaces == (*YDC_QUERY_META, *FROM_YDC_GENERAL)
    assert len(surfaces) == 13
    assert not set(surfaces) & set(YDC_INDEX_AXIS)


def test_every_row_says_it_is_a_query_stopword():
    """`canonical` 은 정본 표기가 아니라 이 표기가 걸리는 **축**이다 -- 축이 하나뿐인 지금도 그
    자리를 비워 두면 다음 축이 `kind` 를 새로 파게 되고, 그러면 버전 축이 또 하나 생긴다."""
    for row in _rows():
        assert row["kind"] == stopwords.KIND, row
        assert row["canonical"] == stopwords.AXIS, row


def test_a_row_that_the_tokenizer_cannot_emit_is_kept_but_says_so(installed):
    """`surface` 는 표기가 아니라 `bm25.tokenize` 가 내놓는 토큰이다. 도달 못 하는 행을 조용히 두면
    다음 사람이 `뭐라` 가 걸리는 줄 알고, 지우면 그 판단이 사라진다 -- `note` 가 그 사이에 선다."""
    emitted = {token for probe in PROBES for token in bm25.tokenize(probe)}
    for row in _rows():
        reachable = row["surface"] in emitted
        assert reachable is (UNREACHABLE not in row["note"]), row
    assert sum(UNREACHABLE in row["note"] for row in _rows()) == 2  # 사람들·뭐라


# ---------- 두 축이 갈린다 ----------


def test_the_query_tokenizer_drops_the_filler_the_index_tokenizer_keeps(installed):
    """이슈의 근거 예시. ydc 실측과 같은 토큰이 나오고, 질의 쪽에서만 필러가 빠진다."""
    assert bm25.tokenize("백탁 관련해서 소비자들이") == ["백탁", "관련", "소비자"]
    assert bm25.tokenize_query("백탁 관련해서 소비자들이") == ["백탁"]


def test_the_index_still_indexes_the_filler(installed):
    """색인에서 빼면 `소비자` 를 직접 찾는 질의를 못 하게 된다 -- 그 질의가 여기서 돈다."""
    index = bm25.Index(["a", "b"], ["소비자 반응이 좋다", "발림성이 좋다"])
    assert "소비자" in index.postings
    assert index.search("소비자")[0][0] == "a"


def test_search_uses_the_query_tokenizer(installed):
    """필러가 든 자연어 질의가 필러만 든 문서를 끌어올리지 않는다."""
    index = bm25.Index(["topic", "filler"], ["백탁이 심하다", "후니는 우리 소비자편이야"])
    assert index.search("백탁 관련해서 소비자들이")[0][0] == "topic"


def test_the_note_names_what_was_dropped_and_says_nothing_otherwise(installed):
    """`search` 의 유일한 창구다. 뺀 것이 없을 때도 찍으면 그 줄은 곧 읽히지 않는 줄이 된다."""
    assert stopwords.query_note("백탁") is None
    assert stopwords.query_note("사람들 반응") is None  # 전부 불용어라 아무것도 안 뺐다
    note = stopwords.query_note("백탁 관련해서 소비자들이")
    assert note is not None
    assert "관련" in note and "소비자" in note and "v1" in note and "백탁" not in note


def test_a_query_that_is_all_stopwords_keeps_its_tokens(installed):
    """빈 질의는 결과 0건이고, 필러가 낀 순위보다 나쁘다."""
    tokens = bm25.tokenize("사람들 반응")
    assert tokens and set(tokens) <= stopwords.active().words
    assert bm25.tokenize_query("사람들 반응") == tokens


def test_without_an_active_list_the_query_tokenizer_is_the_index_tokenizer():
    """활성 버전이 없는 것은 막힘이 아니라 이 목록 이전의 검색이다 -- 주제 사전과 다른 자리다."""
    stopwords.forget()
    assert stopwords.active().version is None
    assert bm25.tokenize_query("백탁 관련해서 소비자들이") == bm25.tokenize("백탁 관련해서 소비자들이")


# ---------- 기준선이 안 움직인다 ----------


def test_no_alias_and_no_eval_query_meets_the_list(installed):
    """`contracts/interfaces.md` §검색 실측 여섯 줄이 안 움직이는 근거. 겹침이 0 이면
    `tokenize_query` 가 `tokenize` 와 같은 토큰을 내므로 재실행 없이 성립한다."""
    aliases = sorted({a for e in topics_module.active().entries for a in e["ko"] + e["latin"]})
    assert len(aliases) == 73
    assert not [a for a in aliases if set(bm25.tokenize(a)) & stopwords.active().words]
    for mode, count in (("literal", 61), ("heldout", 60)):
        queries = retrieval_eval.queries(mode)
        assert len(queries) == count
        assert all(bm25.tokenize_query(q) == bm25.tokenize(q) for _topic, q in queries)


def test_the_heldout_gold_is_defined_on_the_index_axis(installed):
    """`docs_with_tokens` 가 `tokenize_query` 를 타면 heldout 의 정답 집합이 넓어져 .062 가 낡는다."""
    index = bm25.Index(["a", "b"], ["소비자 반응이 좋다", "백탁이 심하다"])
    assert retrieval_eval.docs_with_tokens(index, "소비자 반응") == {"a"}


# ---------- DB 왕복 ----------


@pytest.mark.postgres
def test_the_active_version_is_what_the_lexicon_cli_loaded(conn, needs_runtime_url: str):
    """적재 경로는 `cosmai lexicon load` 하나다 -- 새 CLI 도 새 kind 도 새 DDL 도 필요 없었다
    (`kind='stopword'` 는 001 의 CHECK 에 이미 있다)."""
    argv = ["lexicon", "load", "--kind", stopwords.KIND, "--version", "1"]
    assert main([*argv, str(stopwords.DICTIONARY_CSV), "--url", needs_runtime_url]) == 0
    assert (
        main(["lexicon", "activate", "--kind", stopwords.KIND, "--version", "1", "--url", needs_runtime_url])
        == 0
    )
    loaded = stopwords.load(conn)
    assert loaded.version == 1
    assert loaded.words == csv_stopwords().words


@pytest.mark.postgres
def test_a_loaded_version_does_not_move_the_list_until_it_is_activated(conn, needs_runtime_url: str):
    from db.lexicon import insert_entities

    install_stopwords(conn)
    before = stopwords.load(conn)
    with conn.cursor() as cur:
        wider = [*stopword_rows(), (stopwords.KIND, stopwords.AXIS, "궁금", None, "manual", "")]
        insert_entities(cur, wider, 2, active=False)
    conn.commit()
    assert stopwords.load(conn).words == before.words
    argv = ["lexicon", "activate", "--kind", stopwords.KIND, "--version", "2", "--url", needs_runtime_url]
    assert main(argv) == 0
    after = stopwords.load(conn)
    assert after.version == 2
    assert "궁금" in after.words


@pytest.mark.postgres
def test_an_empty_schema_gives_an_empty_list_instead_of_refusing(conn):
    """주제 사전은 여기서 멈춘다(정답 0건이 거짓 초록을 만든다). 질의 불용어는 멈추지 않는다."""
    empty = stopwords.load(conn)
    assert empty.words == frozenset()
    assert empty.version is None


@pytest.mark.postgres
def test_activating_the_aspect_dictionary_does_not_turn_this_list_off(conn, needs_runtime_url: str):
    """포크 #56 이 aspect v3 를 올리는 동안 이 목록이 꺼지지 않는다는 것. `entity_lexicon` 의
    activate 는 `WHERE kind = %s` 라 두 사전의 버전 축이 아예 다르다."""
    install_topics(conn)
    install_stopwords(conn)
    for version in (2, 3):
        install_topics(conn, version=version, active=False)
    argv = ["lexicon", "activate", "--kind", "aspect", "--version", "3", "--url", needs_runtime_url]
    assert main(argv) == 0
    assert topics_module.load(conn).version == 3
    still = stopwords.load(conn)
    assert still.version == 1
    assert still.words == csv_stopwords().words


@pytest.mark.postgres
def test_the_index_cache_signature_does_not_move_with_the_list(conn):
    """색인은 `tokenize` 그대로라 목록이 바뀌어도 같은 색인이 맞다 -- 서명이 이것을 물면 목록을
    고칠 때마다 38만 청크를 다시 형태소 분석한다."""
    from analysis.retrieval.pipeline import index_signature

    install_topics(conn)
    before = index_signature(conn, None)
    install_stopwords(conn)
    assert index_signature(conn, None) == before
