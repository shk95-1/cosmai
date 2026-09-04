"""Stopwords on the query axis (fork #46). Nothing changes on the index axis here.

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

# The query-meta block appended after `seeds/stopwords_ko.txt` in ydc `v0.3.0`, as it is (12 of them). That
# file is a read-only original, so it is not brought into the repo and lives here only as a test vector -- the
# same place as the 30 particles of `test_particles.py`.
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
# The only one of the general block (30) at the front of the same file that enters our list. ydc applies the
# whole file to queries, so this word dropped out of queries there too, and the example the issue gives is the
# result of dropping it.
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
# The (1) above. The test vector is `GENERAL_CARRIERS` below, and the rest are carried in `<word> is good`
# and measured.
# Only these four are caught by the two-character rule -- the other nine never reach it (they are caught on
# the tag).
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
    """The repo CSV as this process's active list. A test that uses a DB sets it up again in its own
    schema."""
    active = stopwords.use(csv_stopwords())
    yield active
    stopwords.forget()


# ---------- what the list is and where it lives ----------


def test_the_repo_csv_carries_the_ydc_judgement_and_none_of_the_general_block():
    """ydc `v0.3.0` 판과의 대조. 질의 메타 12 + `관련` 이고, 일반어 블록의 나머지 29 는 오지 않는다.
    13 은 계약(`formats.md` §entity 사전의 kind='stopword')과 이슈 본문이 함께 부르는 수다."""
    surfaces = tuple(row["surface"] for row in _rows())
    assert surfaces == (*YDC_QUERY_META, *FROM_YDC_GENERAL)
    assert len(surfaces) == 13
    assert not set(surfaces) & set(YDC_GENERAL_REST)


def test_the_general_block_we_left_behind_is_left_behind_for_the_reason_we_wrote():
    """M6: that the ground is two branches rather than "lift disposed of it". 13 are really dropped by the
    tokenizer, and the other 16 stay as tokens but are common enough that idf discounts them -- the latter is
    the other side of why this list is needed. If the measurement splits, that sentence is the first thing to
    go stale, so it is counted here."""
    emitted, dropped = set(), set()
    for word in YDC_GENERAL_REST:
        text = GENERAL_CARRIERS.get(word, f"{word}은 좋다")
        (emitted if word in bm25.tokenize(text) else dropped).add(word)
    assert dropped == DROPPED_BY_TOKENIZE
    assert len(emitted) == 16
    # Why they are dropped is counted too -- "the two-character rule disposed of them" is true of four only,
    # and the other nine are caught on the tag.
    kiwi = bm25.kiwi()
    by_length_rule = set()
    for word in dropped:
        # kiwipiepy's overloads tie a single-sentence input together with the batch return, so it cannot be
        # narrowed.
        parsed: Any = kiwi.tokenize(GENERAL_CARRIERS.get(word, f"{word}은 좋다"))
        tags = [t.tag.split("-")[0] for t in parsed if t.form == word]
        if any(tag in bm25.KIWI_TAGS for tag in tags):
            by_length_rule.add(word)
    assert by_length_rule == {"등", "때", "중", "점"}
    # 흔해서 걸리는 축은 idf 다. 이 목록의 근거(`소비자`)는 거기 안 걸린다 -- 그 대비가 논지다.
    assert not emitted & set(YDC_QUERY_META)


def test_every_row_says_it_is_a_query_stopword():
    """`canonical` is not the canonical spelling but the **axis** this spelling hangs on -- even now, with
    only one axis, leaving that slot empty makes the next axis dig `kind` open again, and then there is one
    more version axis."""
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


# ---------- the two axes are split ----------


def test_the_query_tokenizer_drops_the_filler_the_index_tokenizer_keeps(installed):
    """The example the issue gives. The same tokens as the ydc measurement come out, and the filler drops on
    the query side only."""
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
    """A natural-language query with fillers in it does not pull up a document holding only fillers."""
    index = bm25.Index(["topic", "filler"], ["백탁이 심하다", "후니는 우리 소비자편이야"])
    assert index.search("백탁 관련해서 소비자들이")[0][0] == "topic"


def test_the_note_names_what_was_dropped_and_says_nothing_otherwise(installed):
    """The one window of `search`. Printed even when nothing was taken out, that line soon stops being
    read."""
    assert stopwords.query_note("백탁") is None
    assert stopwords.query_note("사람들 반응") is None  # 전부 불용어라 아무것도 안 뺐다
    note = stopwords.query_note("백탁 관련해서 소비자들이")
    assert note is not None
    assert "관련" in note and "소비자" in note and "v1" in note and "백탁" not in note


def test_a_query_that_is_all_stopwords_keeps_its_tokens(installed):
    """An empty query is 0 results, and that is worse than a ranking with fillers in it."""
    tokens = bm25.tokenize("사람들 반응")
    assert tokens and set(tokens) <= stopwords.active().words
    assert bm25.tokenize_query("사람들 반응") == tokens


def test_without_an_active_list_the_query_tokenizer_is_the_index_tokenizer():
    """No active version is not a blocker but the search as it was before this list -- unlike the topic
    dictionary."""
    stopwords.forget()
    assert stopwords.active().version is None
    assert bm25.tokenize_query("백탁 관련해서 소비자들이") == bm25.tokenize("백탁 관련해서 소비자들이")


# ---------- the baseline does not move ----------


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


# ---------- the DB round trip ----------


@pytest.mark.postgres
def test_the_active_version_is_what_the_lexicon_cli_loaded(conn, needs_runtime_url: str):
    """There is one load path, `cosmai lexicon load` -- no new CLI, no new kind and no new DDL was needed
    (`kind='stopword'` is already in the CHECK of 001)."""
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
    """The topic dictionary stops here (0 answers make a false green). The query stopwords do not stop."""
    empty = stopwords.load(conn)
    assert empty.words == frozenset()
    assert empty.version is None


@pytest.mark.postgres
def test_activating_the_aspect_dictionary_does_not_turn_this_list_off(conn, needs_runtime_url: str):
    """That this list is not switched off while fork #56 raises aspect v3. The number of `entity_lexicon`
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
    """The index is `tokenize` as it was, so the same index is right even when the list changes -- the
    signature does not bite this."""
    from analysis.retrieval.pipeline import index_signature

    install_topics(conn)
    before = index_signature(conn, None)
    install_stopwords(conn)
    assert index_signature(conn, None) == before
