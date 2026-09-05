"""The place that keeps the decision not to attach a query router (fork #47).

There are two things to keep.

**① 성분명 판정의 정본은 토크나이저 사전이 아니다.** `analysis/retrieval/dict/ingredient_dictionary.tsv`
는 Kiwi 사전이고 담론어를 **일부러** 담는다 -- 없으면 `백탁` 이 `백`+`탁` 으로 쪼개진다(`bm25.kiwi`).
그걸 성분명 목록으로 읽으면 그 말이 든 자연어 질의가 정확 질의로 판정되고, ydc 가 그 함정을 실측으로
밟았다(`rag/router.py` v0.3.0). 우리는 같은 사전을 같은 자리에 두고 있으므로 라우터를 붙이는 날 같은 일이
일어난다. 그래서 이 파일은 라우터가 없는 지금 **미리** 선다.

**2. Are the numbers that decision quoted still true.** Write a number into the contract without leaving a
way to measure it and that number goes quietly false the moment the dictionary grows -- the same place as
`tool/compare-ydc-sensitivity` in #41 and `tool/measure-evidence-fixture` in #6, and here
`tool/measure-query-routing` is that way.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from functools import lru_cache
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType

import pytest

from analysis.retrieval import bm25, topics

ROOT = Path(__file__).resolve().parents[2]
INTERFACES = ROOT / "contracts" / "interfaces.md"
TOOL = ROOT / "tool" / "measure-query-routing"
# The four that ydc's demo nailed down as having to be **absent** from its own ingredient-name list. Our
# dictionary has all four, and that is the evidence this dictionary is not an ingredient-name list -- the same
# words keep the opposite side.
DISCOURSE = ("선크림", "백탁", "톤업", "썬크림")


@lru_cache(maxsize=1)
def measured() -> dict:
    """Calls the tool **once per session** -- it puts Kiwi on, so every call is several seconds."""
    done = subprocess.run(
        [sys.executable, str(TOOL), "--json"], capture_output=True, text=True, cwd=ROOT, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@lru_cache(maxsize=1)
def contract() -> str:
    body = INTERFACES.read_text(encoding="utf-8")
    start = body.index("## Query routing (no router is attached")
    return body[start : body.index("\n## ", start)]


def surfaces() -> set[str]:
    """One ingredient dictionary, not the two tokenizer dictionaries -- the file a router would mistake for an
    ingredient-name list."""
    path = bm25.DICT_DIR / "ingredient_dictionary.tsv"
    lines = path.read_text(encoding="utf-8").splitlines()
    return {line.split("\t")[0] for line in lines if line.strip() and not line.startswith("#")}


def loaded() -> ModuleType:
    """It has no extension, so a plain import does not reach it. Checking exit code 2 needs a constant
    swapped, so it is called inside the process rather than outside it."""
    spec = spec_from_loader("measure_query_routing", SourceFileLoader("measure_query_routing", str(TOOL)))
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_tool_still_runs_and_answers_in_the_shape_the_test_reads():
    got = measured()
    assert set(got) == {"dictionary", "candidates", "sample", "ydc_published", "brand_only"}
    assert got["sample"]["size"] == 10 and got["sample"]["topics"] == 15


def test_a_missing_source_is_blocked_and_not_a_quietly_empty_answer(monkeypatch: pytest.MonkeyPatch):
    """Exiting 0 reads as "I measured it and there is no trap" -- a missing source is a blocker."""
    module = loaded()
    monkeypatch.setattr(module, "INGREDIENTS", ROOT / "analysis" / "retrieval" / "dict" / "없는파일.tsv")
    monkeypatch.setattr(sys, "argv", ["measure-query-routing", "--json"])
    assert module.main() == 2


def test_the_case_against_a_partial_router_is_a_measurement_not_a_flourish():
    """With vector as the default, a partial router makes nothing worse -- this number is what answers
    that."""
    only = measured()["brand_only"]
    assert only["correct"] == 0 and only["misrouted"] > 0, only
    assert only["traces"]["밀려"] == ["려"] and only["traces"]["화이트닝"] == ["화이트"]
    # Short spellings are the cause. Once they all grow long the false hits disappear and this line has to be
    # measured again.
    assert only["short_surfaces"] > 0 and only["short_surfaces"] < only["surfaces"]


def test_the_collagen_seat_is_measured_on_the_axis_that_actually_holds_it():
    """`mfds_inci ∩ 토크나이저 사전` 15 는 순수 INCI 화학명이라 담론어의 **정반대**다. `콜라겐` 자리는
    `mfds_inci ∩ ko` 이고, 그 둘을 바꿔 쓰면 문단이 자기 표와 어긋난다."""
    shape = measured()["dictionary"]
    both = shape["inci_also_asked_with"]
    assert len(both) == 3 and set(both) == {"아보벤존", "옥토크릴렌", "자외선차단제"}
    assert len(both) < shape["inci_in_dictionary"], "두 축이 같아지면 계약 문단을 다시 읽어야 한다"
    assert f"**{len(both)}개** 있다 — `아보벤존`" in contract()
    assert f"`콜라겐` 자리 | **{len(both)}** |" in contract()


def test_the_sample_admits_that_choosing_the_alias_rule_was_also_a_choice():
    """The rule removed the arbitrariness of picking queries by hand, but the arbitrariness of picking the
    rule remains."""
    wobble = measured()["sample"]["alias_rules"]
    assert wobble["rates"]["first_ko"] == measured()["sample"]["misrouted"]
    assert wobble["low"] < wobble["rates"]["first_ko"] < wobble["high"]
    assert f"**{wobble['low']}~{wobble['high']}** 사이에서 움직인다" in contract()


def test_the_tokenizer_dictionary_carries_discourse_words_so_it_is_not_an_ingredient_list():
    """The heart of this file. If even one of the four disappears the dictionary has moved towards an
    ingredient table, and then this decision has to be read again -- it is not passed over quietly."""
    present = surfaces()
    assert bm25.DICT_DIR / "ingredient_dictionary.tsv" in bm25.DICTIONARIES
    for word in DISCOURSE:
        assert word in present, f"{word} 가 사전에서 빠졌다 -- 이 사전의 성격이 바뀌었는지 확인하라"


def test_the_tokenizer_dictionary_overlaps_the_words_people_ask_with():
    """An overlap of 0 means the trap is gone (the same 0 that #46 measured on query stopwords). It is not 0
    now."""
    people = {alias for entry in topics.active().entries for alias in entry["ko"]}
    caught = people & surfaces()
    assert caught, "an overlap of 0 would mean the grounds of §Query routing have changed"
    assert len(caught) == measured()["dictionary"]["ko_in_dictionary"]
    for word in ("선크림", "백탁", "톤업", "무기자차"):
        assert word in caught


def test_no_list_in_this_repo_can_decide_that_a_word_is_an_ingredient():
    """All three candidates hold discourse words. If even one becomes clean, the router's ingredient-name axis
    could open."""
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
    assert inci & people, "if the two axes had parted, the grounds of §Query routing would have changed"


def test_the_frames_carry_no_exact_signal_of_their_own():
    """It shows every time that the ratio does not come from the sentence pattern -- once the pattern touches
    the dictionary, the sample is no longer a sample."""
    assert measured()["sample"]["frame_signals"] == 0


def test_ydc_published_queries_route_the_same_way_on_our_dictionary():
    """The three ydc published have to match that document down to the spellings caught -- it is the same file
    from the same ingredient table."""
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
        ("`ko` 별칭 73개 중 토크나이저 사전에 있는 것 | **11** |", "dictionary", "ko_in_dictionary"),
        ("`mfds_inci` 표기 24개 중 토크나이저 사전에 있는 것 | **15** |", "dictionary", "inci_in_dictionary"),
    ],
)
def test_the_contract_still_says_what_the_tool_measures(sentence: str, section: str, key: str):
    """It looks first at whether the sentence is still there in full -- fixing only the numbers and deleting
    the sentence makes this table close its eyes."""
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
    keys = found["candidates"]["entity_ingredient_keys"]
    # The denominator goes with it -- with only 8 written down it reads against the 28 keys right next to it
    # (the measurement is 8 of the 32 keys of the original CSV).
    assert f"`category='ingredient'` 는 **32키 중 {keys}키**" in table
    assert f"32키의 `category` 가 **{found['candidates']['entity_categories']}종**" in table
    only = found["brand_only"]
    assert f"`kind='brand'` {only['rows']}행 / 고유 표기 {only['surfaces']}" in table
    assert f"**{only['short_surfaces']}개가 2자 이하**" in table
    assert f"주제 별칭 {only['queries']}개에서 `bm25` 로 보내는 질의 | **{only['misrouted']}**" in table
    assert f"그중 옳은 것 **{only['correct']}**" in table


def test_the_decision_and_its_blockers_are_still_written_down():
    """With only the numbers left and the decision gone, the next person just attaches the router."""
    table = contract()
    assert "The canonical decision on an ingredient name is not the tokeniser dictionary" in table
    assert "slopindustries/cosmai#73" in table
    assert "§검색 실측 과 **같은 자가 아니다**" in table
    assert "라우터는 #11 을 대체하지 못한다" in table
    # The misread number (`4/10`) is nailed down as a literal, and without the sentence that defuses it nailed
    # down too it is asymmetric.
    assert "**그래서 이 절의 답은 첫 줄이 아니라 넷째 줄과 마지막 줄이다**" in table
    assert "Not one of the seven lines of the table above is an input to #11" in table
    assert "**표본을 규칙이 만들어도 규칙 선택의 자의성은 남는다.**" in table
    assert "이득 0 · 손해 2" in table
