"""질의 라우터를 붙이지 않는다는 결정을 지키는 자리 (포크 #47).

지킬 것이 둘이다.

**① 성분명 판정의 정본은 토크나이저 사전이 아니다.** `analysis/retrieval/dict/ingredient_dictionary.tsv`
는 Kiwi 사전이고 담론어를 **일부러** 담는다 -- 없으면 `백탁` 이 `백`+`탁` 으로 쪼개진다(`bm25.kiwi`).
그걸 성분명 목록으로 읽으면 그 말이 든 자연어 질의가 정확 질의로 판정되고, ydc 가 그 함정을 실측으로
밟았다(`rag/router.py` v0.3.0). 우리는 같은 사전을 같은 자리에 두고 있으므로 라우터를 붙이는 날 같은 일이
일어난다. 그래서 이 파일은 라우터가 없는 지금 **미리** 선다.

**② 그 결정이 인용한 수가 아직 참인가.** 계약에 숫자를 적고 재는 길을 남기지 않으면 사전이 자라는 순간
그 숫자가 조용히 거짓이 된다 -- #41 의 `tool/compare-ydc-sensitivity`, #6 의 `tool/measure-evidence-fixture`
와 같은 자리이고, 여기서는 `tool/measure-query-routing` 이 그 길이다.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

from analysis.retrieval import bm25, topics

ROOT = Path(__file__).resolve().parents[2]
INTERFACES = ROOT / "contracts" / "interfaces.md"
TOOL = ROOT / "tool" / "measure-query-routing"
# ydc 의 demo 가 자기 성분명 목록에 **없어야 한다**고 못 박은 넷. 우리 사전에는 넷 다 있고, 그것이 이
# 사전이 성분명 목록이 아니라는 증거다 -- 같은 낱말로 반대편을 지킨다.
DISCOURSE = ("선크림", "백탁", "톤업", "썬크림")


@lru_cache(maxsize=1)
def measured() -> dict:
    """도구를 **세션에 한 번만** 부른다 -- Kiwi 를 얹는 일이라 부를 때마다 몇 초다."""
    done = subprocess.run(
        [sys.executable, str(TOOL), "--json"], capture_output=True, text=True, cwd=ROOT, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@lru_cache(maxsize=1)
def contract() -> str:
    body = INTERFACES.read_text(encoding="utf-8")
    start = body.index("## 질의 라우팅 (라우터를 붙이지 않는다")
    return body[start : body.index("\n## ", start)]


def surfaces() -> set[str]:
    """토크나이저 사전 두 벌이 아니라 성분 사전 한 벌 -- 라우터가 성분명 목록으로 오해할 그 파일이다."""
    path = bm25.DICT_DIR / "ingredient_dictionary.tsv"
    lines = path.read_text(encoding="utf-8").splitlines()
    return {line.split("\t")[0] for line in lines if line.strip() and not line.startswith("#")}


def test_the_tool_still_runs_and_answers_in_the_shape_the_test_reads():
    got = measured()
    assert set(got) == {"dictionary", "candidates", "sample", "ydc_published"}
    assert got["sample"]["size"] == 10 and got["sample"]["topics"] == 15


def test_the_tokenizer_dictionary_carries_discourse_words_so_it_is_not_an_ingredient_list():
    """이 파일의 핵심. 넷 중 하나라도 사라지면 사전이 성분표 쪽으로 움직인 것이고, 그때는 이 결정을
    다시 읽어야 한다 -- 조용히 통과시키지 않는다."""
    present = surfaces()
    assert bm25.DICT_DIR / "ingredient_dictionary.tsv" in bm25.DICTIONARIES
    for word in DISCOURSE:
        assert word in present, f"{word} 가 사전에서 빠졌다 -- 이 사전의 성격이 바뀌었는지 확인하라"


def test_the_tokenizer_dictionary_overlaps_the_words_people_ask_with():
    """겹침이 0 이 되면 함정이 사라진 것이다(#46 이 질의 불용어에서 잰 0 과 같은 자). 지금은 0 이 아니다."""
    people = {alias for entry in topics.active().entries for alias in entry["ko"]}
    caught = people & surfaces()
    assert caught, "겹침이 0 이면 §질의 라우팅 의 근거가 바뀐 것이다"
    assert len(caught) == measured()["dictionary"]["ko_in_dictionary"]
    for word in ("선크림", "백탁", "톤업", "무기자차"):
        assert word in caught


def test_no_list_in_this_repo_can_decide_that_a_word_is_an_ingredient():
    """후보 셋이 전부 담론어를 담는다. 하나라도 깨끗해지면 라우터의 성분명 축이 열릴 수 있다."""
    from db.seed.lexicon import _ingredient_rows

    rows = _ingredient_rows(ROOT / "eval")
    surface, key = 2, 1
    keys_of = {}
    for row in rows:
        keys_of.setdefault(str(row[surface]), set()).add(str(row[key]))
    # `무기자차` 는 담론어인데 성분 키 **둘**의 표기다 -- 어느 성분인지 이 목록으로는 못 정한다.
    assert len(keys_of["무기자차"]) > 1, keys_of["무기자차"]

    catalogue = list(
        csv.DictReader((ROOT / "eval/lexicon/ingredient_kr_colloquial_v1.csv").open(encoding="utf-8"))
    )
    actual = [row for row in catalogue if row["category"] == "ingredient"]
    assert len(actual) < len(catalogue) / 2, "성분이 아닌 항목이 절반을 넘는다 -- 성분표가 아니다"

    inci = {alias for entry in topics.active().entries for alias in entry["mfds_inci"]}
    people = {alias for entry in topics.active().entries for alias in entry["ko"]}
    assert "자외선차단제" in inci, "제품 범주가 성분 표기 축에 섞여 있다"
    assert inci & people, "두 축이 갈렸다면 §질의 라우팅 의 근거가 바뀐 것이다"


def test_the_frames_carry_no_exact_signal_of_their_own():
    """비율이 문형에서 온 것이 아님을 매번 보인다 -- 문형이 사전을 건드리면 표본이 표본이 아니다."""
    assert measured()["sample"]["frame_signals"] == 0


def test_ydc_published_queries_route_the_same_way_on_our_dictionary():
    """ydc 가 공표한 셋은 걸린 표기까지 그쪽 문서와 같아야 한다 -- 같은 성분표에서 나온 같은 파일이다."""
    published = measured()["ydc_published"]
    assert published["misrouted"] == published["queries"] == 3
    assert published["traces"]["선크림 루틴 알려줘"] == ["선크림", "루틴"]
    assert published["traces"]["백탁 관련해서 소비자들이 뭐라고 해"] == ["백탁"]
    assert published["traces"]["끈적이지 않는 선크림 추천"] == ["선크림"]


@pytest.mark.parametrize(
    ("sentence", "section", "key"),
    [
        ("오라우팅 **4/10**", "sample", "misrouted"),
        ("| **7/15** |", "sample", "topic_misrouted"),
        ("| **3/3** |", "ydc_published", "misrouted"),
        ("`ko` 별칭 67개 중 토크나이저 사전에 있는 것 | **11** |", "dictionary", "ko_in_dictionary"),
        ("`mfds_inci` 표기 24개 중 토크나이저 사전에 있는 것 | **15** |", "dictionary", "inci_in_dictionary"),
    ],
)
def test_the_contract_still_says_what_the_tool_measures(sentence: str, section: str, key: str):
    """문장이 통째로 남아 있는지부터 본다 -- 숫자만 고치고 문장을 지우면 이 표가 눈을 감는다."""
    assert sentence in contract(), sentence
    assert str(measured()[section][key]) in sentence


def test_every_number_the_routing_table_cites_is_the_number_the_tool_measures():
    table = contract()
    found = measured()
    assert f"오라우팅 **{found['sample']['misrouted']}/{found['sample']['size']}**" in table
    assert f"| **{found['sample']['topic_misrouted']}/{found['sample']['topics']}** |" in table
    assert f"`ko` 별칭 {found['dictionary']['ko_aliases']}개" in table
    assert f"`mfds_inci` 표기 {found['dictionary']['inci_surfaces']}개" in table
    assert f"{found['dictionary']['surfaces']:,}표기" in table
    assert f"{found['candidates']['entity_rows']}행 / {found['candidates']['entity_keys']}키" in table
    assert f"`category='ingredient'` 는 **{found['candidates']['entity_ingredient_keys']}키**" in table


def test_the_decision_and_its_blockers_are_still_written_down():
    """수만 남고 결정이 사라지면 다음 사람이 라우터를 그냥 붙인다."""
    table = contract()
    assert "성분명 판정의 정본은 토크나이저 사전이 아니다" in table
    assert "slopindustries/cosmai#73" in table
    assert "§검색 실측 과 **같은 자가 아니다**" in table
    assert "라우터는 #11 을 대체하지 못한다" in table
