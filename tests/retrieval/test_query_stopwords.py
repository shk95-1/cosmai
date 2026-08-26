"""질의 축의 불용어 (포크 #46). 색인 축은 여기서 아무것도 안 바뀐다.

두 축이 다르다는 것이 이 파일의 본론이다. `bm25.tokenize` 는 그대로 두고 `bm25.tokenize_query` 만
목록을 탄다 -- 색인에서 `소비자` 를 빼면 `소비자` 를 직접 찾는 질의를 못 하게 된다. 목록이 사는 자리는
`needs.entity_lexicon` 의 `kind='stopword'` · `canonical='query'` 활성 버전이고, **활성 버전**이 aspect
사전과 따로 돈다(`entity_lexicon` 의 activate 는 kind 단위)는 것도 여기서 못 박는다 -- 포크 #56 이 같은
시기에 aspect v3 를 올리는데, 한쪽을 켜는 일이 다른 쪽을 끄면 안 된다. **버전 번호표까지 따로인 것은
아니다**: `analysis/lexicon.py` 의 `_label` 과 `aggregate/pipeline.py:149` 가 kind 를 안 가리고
`max(version)` 을 읽는다 -- 물려받은 성질이라 포크 #58 이 지고, 계약이 그 사실을 적는다.
"""

from __future__ import annotations

import csv
from typing import Any

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
# 그 일반어 블록의 나머지 29. 우리는 안 가져왔고, 근거는 두 갈래다(등급 B 리뷰 M6 이 고친 자리 --
# "lift 가 처분했다"는 **틀렸다**: lift 는 `terms` 의 미포착 표현 보고서에서만 돌고 BM25 점수에는 닿지
# 않는다). (1) 13개는 `bm25.tokenize` 가 실제로 버린다 -- 아홉은 `KIWI_TAGS` 밖 태그(의존명사 NNB ·
# 대명사 NP · 접미사 XSV)라서, 넷(`등`·`때`·`중`·`점`)은 한 글자 명사의 2글자 규칙이라서다(아래 실측).
# (2) 남는 16개는 토큰으로 살아 있지만 **흔해서 잡음인 부류**(유튜브 상투어·시간 부사)라 idf 가 깎는
# 축에 걸린다. 이 목록이 필요한 이유가 정확히 그 반대편이다 -- `소비자`(df 289 < `백탁` 338)는 흔하지
# 않아서 idf 로 안 걸리고, 그래서 판단으로 넣어야 한다.
YDC_GENERAL_REST = (
    "것", "수", "등", "때", "거", "분", "번", "중", "점", "쪽", "듯",
    "영상", "댓글", "채널", "오늘", "이번", "지금", "그냥", "더", "또",
    "여러분", "안녕", "구독", "링크", "클릭", "확인", "하", "되", "있",
)  # fmt: skip
# 위 (1). 시험 벡터는 아래 `GENERAL_CARRIERS` 이고, 나머지는 `<말>은 좋다`로 담아 잰다.
# 2글자 규칙에 걸리는 것은 이 넷뿐이다 -- 나머지 아홉은 그 규칙까지 가지도 않는다(태그에서 걸린다).
DROPPED_BY_TOKENIZE = frozenset(
    {"것", "수", "등", "때", "거", "분", "번", "중", "점", "쪽", "듯", "여러분", "하"}
)
GENERAL_CARRIERS = {
    "하": "이 제품 사용해서 좋다",
    "되": "잘 발려서 됐다",
    "있": "촉촉함이 있어요",
    "더": "더 촉촉하다",
    "또": "또 샀다",
    "여러분": "여러분 안녕하세요",
}

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


def test_the_repo_csv_carries_the_ydc_judgement_and_none_of_the_general_block():
    """ydc `v0.3.0` 판과의 대조. 질의 메타 12 + `관련` 이고, 일반어 블록의 나머지 29 는 오지 않는다.
    13 은 계약(`formats.md` §entity 사전의 kind='stopword')과 이슈 본문이 함께 부르는 수다."""
    surfaces = tuple(row["surface"] for row in _rows())
    assert surfaces == (*YDC_QUERY_META, *FROM_YDC_GENERAL)
    assert len(surfaces) == 13
    assert not set(surfaces) & set(YDC_GENERAL_REST)


def test_the_general_block_we_left_behind_is_left_behind_for_the_reason_we_wrote():
    """M6: 근거가 "lift 가 처분했다"가 아니라 두 갈래라는 것. 13은 토크나이저가 실제로 버리고,
    16은 토큰으로 남되 흔해서 idf 가 깎는 부류다 -- 후자가 이 목록이 필요한 이유의 반대편이다.
    실측이 갈라지면 그 문장부터 낡은 것이므로 여기서 센다."""
    emitted, dropped = set(), set()
    for word in YDC_GENERAL_REST:
        text = GENERAL_CARRIERS.get(word, f"{word}은 좋다")
        (emitted if word in bm25.tokenize(text) else dropped).add(word)
    assert dropped == DROPPED_BY_TOKENIZE
    assert len(emitted) == 16
    # 왜 버려지는지까지 센다 -- "2글자 규칙이 처분했다"는 넷에만 맞는 말이고, 아홉은 태그에서 걸린다.
    kiwi = bm25.kiwi()
    by_length_rule = set()
    for word in dropped:
        # kiwipiepy 의 오버로드는 한 문장 입력도 배치 반환과 함께 묶어 놓아 좁혀지지 않는다(bm25 와 같다).
        parsed: Any = kiwi.tokenize(GENERAL_CARRIERS.get(word, f"{word}은 좋다"))
        tags = [t.tag.split("-")[0] for t in parsed if t.form == word]
        if any(tag in bm25.KIWI_TAGS for tag in tags):
            by_length_rule.add(word)
    assert by_length_rule == {"등", "때", "중", "점"}
    # 흔해서 걸리는 축은 idf 다. 이 목록의 근거(`소비자`)는 거기 안 걸린다 -- 그 대비가 논지다.
    assert not emitted & set(YDC_QUERY_META)


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
    """색인에서 빼면 `소비자` 를 직접 찾는 질의를 못 하게 된다 -- 그 질의가 여기서 돈다.

    두 단언이 다른 변이를 잡는다(리뷰 M8): postings 는 "색인도 필터를 탄다"를, search 는
    `tokenize_query` 에서 `kept or tokens` 를 뺀 변이를 잡는다 -- 그때 이 질의는 토큰 0개가 되어
    결과가 빈다.
    """
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
    assert len(aliases) == 80  # v1 의 73 + 포크 #56 의 일곱
    assert not [a for a in aliases if set(bm25.tokenize(a)) & stopwords.active().words]
    for mode, count in (("literal", 63), ("heldout", 62)):
        queries = retrieval_eval.queries(mode)
        assert len(queries) == count
        assert all(bm25.tokenize_query(q) == bm25.tokenize(q) for _topic, q in queries)


def test_the_heldout_gold_is_defined_on_the_index_axis(installed):
    """`docs_with_tokens` 가 `tokenize_query` 를 타면 heldout 이 뺄 문서가 **줄어** 정답이 넓어지고
    벡터 채택의 근거였던 .062 가 다른 판 위의 숫자가 된다.

    질의에 **주제어가 섞여 있어야** 그 변이가 관측된다 -- `소비자 반응` 처럼 전부 불용어인 질의는
    `kept or tokens` 규칙에 걸려 두 토크나이저가 같은 답을 내므로 축을 바꿔도 아무 일이 안 일어난다
    (등급 B 리뷰가 잡은 자리, 2026-08-26). `백탁` 을 섞으면 축이 갈린다:
    `tokenize` -> `['백탁','소비자','반응']` 은 두 문서를 다 빼고, `tokenize_query` -> `['백탁']` 은
    `백탁` 이 든 문서 하나만 뺀다.
    """
    index = bm25.Index(["a", "b"], ["소비자 반응이 좋다", "백탁이 심하다"])
    assert bm25.tokenize_query("백탁 소비자 반응") != bm25.tokenize("백탁 소비자 반응")  # 변이가 보이는 판
    assert retrieval_eval.docs_with_tokens(index, "백탁 소비자 반응") == {"a", "b"}


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
    activate 는 `WHERE kind = %s` 라 **활성 버전**이 kind 마다 독립이다. 버전 **번호표**까지 독립인
    것은 아니고(포크 #58), 그 사실은 여기가 아니라 계약이 진다."""
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
