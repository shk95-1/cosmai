"""코퍼스에 없는 이름이 든 질의를 검색이 막는가 (포크 #48).

하한선은 못 쓴다(계약 §벡터 하한선). 코사인이 안 갈리는 자리에서 갈리는 것은 **문서빈도**이고, 이
파일은 그 게이트가 **막아야 하는 것을 막고 막으면 안 되는 것을 통과시키는지**를 본다. 둘 다 실측이
정한 자리다 -- 특히 통과시켜야 하는 쪽(토큰 0개, 짧은 미등장 토큰)은 막는 순간 벡터가 유일하게 답하는
질의를 잃으므로, 여기서 매번 확인한다.

색인은 이 파일이 손으로 세운다. 게이트가 보는 것은 `Index.postings` 의 df 하나라, 코퍼스 전체가 아니라
"그 말이 든 문서가 있는가/없는가" 만 있으면 같은 판정이 선다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from analysis.retrieval import bm25, grounding

ROOT = Path(__file__).resolve().parents[2]
INTERFACES = ROOT / "contracts" / "interfaces.md"
CORPUS = (
    "백탁 없는 선크림 추천합니다 추출물 함유 제품이에요",
    "이 제품 함유 성분이 궁금해요 백탁 심해요",
    "톤업 되는 제품 발림성도 좋아요",
)


@pytest.fixture
def index() -> bm25.Index:
    return bm25.Index([f"d:{i}#0" for i in range(len(CORPUS))], list(CORPUS))


def test_a_name_the_corpus_never_says_stops_the_query(index: bm25.Index):
    found = grounding.check("퀀텀펩타이드사이드", index)
    assert not found.ok
    assert found.missing == ("퀀텀펩타이드사이드",)
    assert "퀀텀펩타이드사이드" in found.note


def test_a_fake_name_hidden_in_a_sentence_is_stopped_too(index: bm25.Index):
    """ydc 초판이 놓친 자리다 -- `함유`·`제품` 의 df 가 0 이 아니라 "전부 0" 규칙을 빠져나갔다.
    우리 규칙은 **하나라도** 0 이므로 문장에 섞인 이름도 잡는다."""
    assert index.postings["함유"] and index.postings["제품"]
    found = grounding.check("퀀텀펩타이드사이드 함유 제품 있어", index)
    assert not found.ok and found.missing == ("퀀텀펩타이드사이드",)


def test_a_real_word_the_corpus_says_passes(index: bm25.Index):
    found = grounding.check("백탁 없는 선크림", index)
    assert found.ok and not found.missing
    assert str(len(index.postings["백탁"])) in found.note, "통과도 근거를 말해야 한다"


def test_a_short_token_the_corpus_lacks_does_not_stop_the_query(index: bm25.Index):
    """길이 4 는 이득 0 · 손해 2 로 정했다 -- 3 으로 내리면 `재도포`·`ZnO` 가 막히는데 가짜 차단은
    그대로다. 짧은 토큰의 df 0 은 "없는 이름" 의 근거로 약하다."""
    assert not index.postings.get("재도포")
    assert len("재도포") < grounding.ZERO_DF_MINLEN
    found = grounding.check("재도포", index)
    assert found.ok and not found.missing


def test_a_query_with_no_tokens_is_not_judged_by_df(index: bm25.Index):
    """`톤 업` 은 벡터가 P@10 1.0 으로 답하는 자리인데 bm25 는 결과가 0건이다. df 로 막으면 벡터가
    유일하게 답하는 질의를 잃는다."""
    assert bm25.tokenize("톤 업") == []
    found = grounding.check("톤 업", index)
    assert found.ok and not found.missing
    assert "토큰" in found.note


def test_the_gate_reads_df_on_the_index_axis_not_the_query_stopword_axis(index: bm25.Index):
    """df 는 색인이 세운 사실이라 색인의 토큰화로 물어야 한다 -- `eval.docs_with_tokens` 와 같은 자리.
    질의 축으로 물으면 불용어 목록이 바뀌는 날 이 게이트의 판정이 함께 흔들린다: 아래 질의는 색인
    축에서 막히고 질의 축에서는 통과한다(`화이트닝` 이 빠지면 남는 토큰의 df 가 0 이 아니다)."""
    from analysis.retrieval import stopwords

    stopwords.use(stopwords.QueryStopwords(frozenset({"화이트닝"}), 1))
    try:
        assert bm25.tokenize("화이트닝 백탁") == ["화이트닝", "백탁"]
        assert bm25.tokenize_query("화이트닝 백탁") == ["백탁"]
        found = grounding.check("화이트닝 백탁", index)
        assert not found.ok and found.missing == ("화이트닝",)
    finally:
        stopwords.forget()


def test_the_search_path_stops_before_it_prints_anything(monkeypatch: pytest.MonkeyPatch, capsys):
    """게이트가 `ranked_chunks` 앞에 서야 한다. 뒤에 서면 순위를 다 매긴 뒤에 버리는 것이라 느리고,
    무엇보다 그 순위가 어딘가에 인쇄될 길이 남는다."""
    from analysis.retrieval import pipeline

    built = bm25.Index([f"d:{i}#0" for i in range(len(CORPUS))], list(CORPUS))
    monkeypatch.setattr(pipeline, "load_index", lambda *a, **k: (built, {}))

    def refuse(*_args, **_kwargs):
        raise AssertionError("막힌 질의는 순위를 매기지 않는다")

    monkeypatch.setattr(pipeline, "ranked_chunks", refuse)
    assert pipeline.search(cast(Any, object()), "퀀텀펩타이드사이드 함유 제품 있어", engine="vector") == []
    assert "퀀텀펩타이드사이드" in capsys.readouterr().err


def test_the_search_path_still_answers_a_grounded_query(monkeypatch: pytest.MonkeyPatch):
    """막는 쪽만 시험하면 게이트를 항상-막힘으로 고쳐도 초록이다."""
    from analysis.retrieval import pipeline

    built = bm25.Index([f"d:{i}#0" for i in range(len(CORPUS))], list(CORPUS))
    monkeypatch.setattr(pipeline, "load_index", lambda *a, **k: (built, {}))
    monkeypatch.setattr(pipeline, "ranked_chunks", lambda *a, **k: [("d:0#0", 1.5)])

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, *_):
            return None

        def fetchall(self):
            return [("d:0#0", CORPUS[0])]

    class Conn:
        def cursor(self):
            return Cursor()

        def commit(self):
            return None

    assert pipeline.search(cast(Any, Conn()), "백탁 없는 선크림", engine="vector") == [
        ("d:0#0", 1.5, CORPUS[0])
    ]


def test_a_blocked_query_is_partial_at_the_command_line(monkeypatch: pytest.MonkeyPatch, capsys):
    """막힌 질의의 답은 결과 0건이고, 그 자리의 종료 코드는 이미 계약에 있다(1) -- 게이트가 새 코드를
    늘리지 않는다는 것을 명령줄 끝에서 확인한다."""
    from analysis.retrieval import pipeline
    from cosmai import cli

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    built = bm25.Index([f"d:{i}#0" for i in range(len(CORPUS))], list(CORPUS))
    monkeypatch.setattr(cli, "_connect", lambda _url: FakeConn())
    monkeypatch.setattr(pipeline, "load_index", lambda *a, **k: (built, {}))
    argv = ["retrieval", "search", "--engine", "vector", "--query", "퀀텀펩타이드사이드 함유 제품 있어"]
    assert cli.main(argv) == 1
    printed = capsys.readouterr()
    assert "결과 없음" in printed.out
    assert "퀀텀펩타이드사이드" in printed.err, "왜 0건인지 말하지 않으면 색인이 빈 것과 구분되지 않는다"


def test_the_search_section_carries_the_gate_and_what_it_costs():
    """`--engine vector` 가 색인을 열게 된 것은 사람이 겪는 변화다 -- 계약에 없으면 캐시 없는 호스트에서
    십수 분을 만나고 나서야 알게 된다."""
    body = (ROOT / "contracts" / "entrypoints.md").read_text(encoding="utf-8")
    start = body.index("## 검색 (")
    search = body[start : body.index("\n## ", start)]
    assert "근거 없는 질의를 df 로 막는다" in search
    assert "새 코드가 늘지 않는다" in search
    assert "`--engine vector` 도 BM25 색인을 연다" in search
    assert "`retrieval eval` 은 이 게이트를 타지 않는다" in search


def test_the_contract_carries_the_rule_and_the_numbers_it_was_chosen_by():
    body = INTERFACES.read_text(encoding="utf-8")
    start = body.index("### 그러면 근거 없는 질의는 무엇이 막는가")
    section = body[start : body.index("\n## ", start)]
    assert f"길이 ≥ {grounding.ZERO_DF_MINLEN} (`ZERO_DF_MINLEN`)" in section
    # 규칙을 고르며 버린 두 갈래가 왜 버려졌는지가 이 절의 절반이다.
    assert "이득 0 · 손해 2" in section
    assert '"전부 0" 갈래는 두지 않는다' in section
    assert "토큰이 0개인 질의는 df 로 판정하지 않는다" in section
    assert "키릴 표기 하나다" in section, "못 막는 자리를 안 적으면 이 게이트가 다 막는 것으로 읽힌다"
