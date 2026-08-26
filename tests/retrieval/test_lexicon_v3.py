"""주제 사전 v3 의 판정 원장이 사전과 같은 것을 말하는가 (포크 #56).

#37 이 ydc `lexicon.json` 을 폐기하며 별칭 5종을 "갈 자리가 없다"로 남겼고, `protected` 32 에서 후보
7종을 남겼다. 이 이슈가 그 열둘(+ #37 이 이미 기각한 3종)에 **등재 / 보류 / 미등재** 하나씩을 붙인다.
원장은 `tool/measure-lexicon-candidates` 의 `LEDGER` 이고 계약 문장은 `contracts/formats.md`
§주제 사전 v3 다. 여기서 되묻는 것은 셋이다.

1. 원장이 **등재**라 적은 표기는 적재 원본에 행으로 있고, **아닌** 표기는 행이 없다.
2. 적재 원본은 얼어붙은 v1(`frozen_topics.py`)에 **이 원장이 적은 것만** 더한 사전이다 -- 그 등식이
   깨지면 `contracts/interfaces.md` §검색 실측 여섯 줄이 어느 사전 위의 값인지 말할 수 없게 된다.
3. 미등재 근거 중 **코드로 다시 물을 수 있는 것**은 코드가 묻는다: `톤업크림` 은 `톤업` 이 이미 보고,
   `sunstick`·`케미컬` 은 레포 자신의 바닥(`terms.MIN_DOCS`) 아래이며, `올영` 은 자리(kind=brand)가
   있어도 `올리브영` 이 `tier='stop'` 이라 링커가 그 표면을 아예 안 본다.

df 자체는 여기서 못 잰다 -- 코퍼스 26만 문서는 레포에 없다(`STATE.md` §3, 읽기 전용 인계본). 그 길은
도구이고, 이 파일은 **도구가 세는 규칙**을 작은 코퍼스 위에서 되묻는다.
"""

from __future__ import annotations

import json
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from typing import Any, cast

import pytest

from analysis.lexicon import compile_lexicon
from analysis.retrieval import bm25, topics
from analysis.retrieval.terms import MIN_DOCS
from analysis.types import EntitySurface
from tests.retrieval import frozen_topics
from tests.retrieval.conftest import csv_rows, csv_topics

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tool" / "measure-lexicon-candidates"


def _tool() -> Any:
    """도구를 모듈로 연다 -- 이름에 하이픈이 있어 import 할 수 없다(`test_query_routing.py` 와 같은 길)."""
    spec = spec_from_loader(
        "measure_lexicon_candidates", SourceFileLoader("measure_lexicon_candidates", str(TOOL))
    )
    assert spec is not None
    module = module_from_spec(spec)
    sys.modules.setdefault("measure_lexicon_candidates", module)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


LEDGER = _tool().LEDGER
LISTED = [row for row in LEDGER if row.verdict == "등재"]
NOT_LISTED = [row for row in LEDGER if row.verdict != "등재"]


def expected_entries() -> list[dict]:
    """얼어붙은 v1 + 이 원장이 적은 것. `test_topics.py` 가 적재 원본과 DB 판을 이것과 맞댄다."""
    added: list[dict] = []
    for frozen in frozen_topics.TOPICS:
        entry = {key: list(value) if isinstance(value, list) else value for key, value in frozen.items()}
        for row in LISTED:
            if row.place == entry["topic"]:
                entry[row.kind] = [*entry[row.kind], row.term]
        added.append(entry)
    return added


def _rows_by_term() -> dict[str, tuple[Any, ...]]:
    from db.lexicon import ASPECT_COLUMNS

    pattern = ASPECT_COLUMNS.index("pattern")
    return {str(row[pattern]): row for row in csv_rows()}


# ---------- 원장과 적재 원본이 같은 것을 말하는가 ----------


def test_every_listed_term_lives_as_a_row_in_the_loading_source():
    """등재 판정은 파일의 한 줄로 살아야 한다 -- 판정만 이슈에 적히면 #9 가 슬라이스를 지우는 순간
    그 표면형이 사라진다(#37 1c 가 남긴 바로 그 자리)."""
    from db.lexicon import ASPECT_COLUMNS

    aspect, extra = (ASPECT_COLUMNS.index(c) for c in ("aspect", "extra"))
    rows = _rows_by_term()
    for row in LISTED:
        assert row.term in rows, f"{row.term} 이 {topics.DICTIONARY_CSV.name} 에 없다"
        csv_row = rows[row.term]
        assert csv_row[aspect] == row.place, row.term
        assert csv_row[extra]["term_kind"] == row.kind, row.term


def test_no_refused_term_slipped_into_the_loading_source():
    """보류·미등재는 근거가 있는 판정이다 -- 행이 조용히 생기면 그 근거가 거짓이 된다."""
    rows = _rows_by_term()
    assert [row.term for row in NOT_LISTED if row.term in rows] == []


def test_the_loading_source_is_the_frozen_v1_plus_exactly_this_ledger():
    """v1 과의 차이가 **정확히 이 원장**이라는 것. 여기가 깨지면 `contracts/interfaces.md`
    §검색 실측 여섯 줄이 어느 사전 위의 값인지 말할 수 없다."""
    loaded = csv_topics()
    assert [e["topic"] for e in loaded.entries] == [e["topic"] for e in frozen_topics.TOPICS]
    for got, want in zip(loaded.entries, expected_entries(), strict=True):
        assert got["ko"] == want["ko"], got["topic"]
        assert got["latin"] == want["latin"], got["topic"]
        assert got["topic_type"] == want["topic_type"], got["topic"]
        assert got["trend_use"] == want["trend_use"], got["topic"]


def test_the_ledger_only_touches_topics_that_already_exist():
    """새 주제를 세우는 것은 별칭을 옮기는 일이 아니다 -- 보류 셋(`화잘먹`·`비비크림`)이 그 자리다."""
    known = {e["topic"] for e in frozen_topics.TOPICS} | {"brand"}
    assert [row.term for row in LEDGER if row.place not in known] == []


# ---------- 미등재 근거를 코드가 되묻는가 ----------


def test_a_term_its_topic_already_sees_earns_no_row():
    """`톤업크림` 의 미등재 근거. `톤업` 이 그 문자열을 이미 부분문자열로 잡으므로 행을 더해도
    그 주제가 새로 보는 문서가 없다 -- 변이(`파데프리`)를 옆에 두어 이 물음이 공회전이 아님을 보인다."""
    assert "톤업_메이크업베이스" in frozen_topics.match_topics("톤업크림", include_excluded=True)
    assert frozen_topics.match_topics("파데프리", include_excluded=True) == []


def test_the_floor_the_ledger_refuses_on_is_the_repos_own_floor():
    """`sunstick` 3 · `케미컬` 3 을 기각한 바닥은 이 이슈가 지어낸 수가 아니라 `terms.MIN_DOCS` 다."""
    assert MIN_DOCS == 5
    below = {row.term: row.df for row in NOT_LISTED if row.df < MIN_DOCS}
    assert below == {"sunstick": 3, "케미컬": 3, "olive영": 0}
    assert all(row.df >= MIN_DOCS for row in LISTED)


def test_a_compound_that_adds_no_document_can_still_earn_its_row_on_the_token_axis(repo_dictionary):
    """`속건조` 는 신규 문서가 0 이라 매칭 축에서는 아무것도 안 바꾼다. 등재 근거는 토큰 축이다 --
    Kiwi 가 `속`+`건조` 로 쪼개던 것을 한 토큰으로 주고, 확장이 `건조` 를 그대로 지킨다."""
    tokens = bm25.tokenize("속건조가 심해요")
    assert "속건조" in tokens and "건조" in tokens


def test_a_stop_tier_canonical_keeps_every_surface_of_its_out_of_the_linker():
    """`올영` 미등재의 근거. 자리(kind=brand)는 있지만 `올리브영` 은 유통 채널이라 `tier='stop'` 이고,
    `compile_lexicon` 이 그 정본의 표면을 `surface_re` 에서 통째로 뺀다 -- 행을 더해도 링커가 그것을
    볼 일이 없다. 변이: tier 를 normal 로 돌리면 바로 잡힌다(그러면 유통 채널이 브랜드 집계에 든다)."""
    stopped = [
        EntitySurface("brand", "올리브영", "올리브영", "stop", "oliveyoung"),
        EntitySurface("brand", "올리브영", "올영", "stop", "oliveyoung"),
    ]
    assert compile_lexicon(stopped, 1).surface_re.search("올영 세일 갔어요") is None
    linkable = [EntitySurface(s.kind, s.canonical, s.surface, "normal", s.source) for s in stopped]
    assert compile_lexicon(linkable, 1).surface_re.search("올영 세일 갔어요") is not None


# ---------- 도구가 세는 규칙 ----------


# 인계본과 같은 열 순서. 손으로 다시 적으면 도구가 읽는 스키마와 픽스처가 조용히 갈린다.
CORPUS_HEADER = (ROOT / "tests" / "fixtures" / "yt_handoff" / "document.csv").read_text(
    encoding="utf-8-sig"
).splitlines()[0] + "\n"
PROBES = (
    "썬쿠션 하나 샀어요",  # 선크림이 못 보던 문서 -- 다른 주제도 안 걸린다
    "sunscreens 여러 개 비교",  # 경계 매칭이면 sunscreen 이 아니다
    "sunscreen 하나 챙겼어요",  # 경계 매칭으로 잡힌다
    "선크림 중에 썬쿠션이 제일 낫다",  # 이미 선크림이 보는 문서
    "속건조가 너무 심해요",  # 건조가 이미 보는 문서
)


def _corpus(tmp_path: Path) -> Path:
    path = tmp_path / "document.csv"
    rows = "".join(f"d{i},youtube_comment,c{i},comment,,,,,{text},,{{}}\n" for i, text in enumerate(PROBES))
    path.write_text(CORPUS_HEADER + rows, encoding="utf-8")
    return path


def test_the_tool_counts_latin_on_the_boundary_rule_the_dictionary_matches_on(tmp_path):
    """`sunscreen` 원장 값이 81 이 아니라 76 인 이유. 부분문자열로 세면 복수형 `sunscreens` 가 끼는데
    사전은 경계로 매칭하므로 그 문서는 사전이 안 보는 문서다 -- 두 수를 나란히 놓으면 축이 갈린다."""
    tool = _tool()
    measured = tool.measure(_corpus(tmp_path), tool.baseline())
    assert measured["documents"] == len(PROBES)
    assert measured["df"]["sunscreen"] == 1  # `sunscreens` 는 안 센다
    assert measured["df"]["썬쿠션"] == 2


def test_the_tool_separates_what_a_row_adds_from_what_the_topic_already_sees(tmp_path):
    """`new` 가 `df` 와 다른 수라는 것. 같으면 `톤업크림`(628편 전부를 `톤업` 이 본다)을 기각한 근거가
    사라진다."""
    tool = _tool()
    measured = tool.measure(_corpus(tmp_path), tool.baseline())
    assert measured["new"]["썬쿠션"] == 1  # 두 문서 중 `선크림` 이 든 쪽은 이미 보인다
    assert measured["new"]["속건조"] == 0  # `건조` 가 이미 본다
    assert measured["unseen"]["썬쿠션"] == 1


def test_the_tool_refuses_when_a_listed_term_falls_through_the_floor(tmp_path):
    """코퍼스가 자라 원장이 거짓이 되는 자리. 종료 코드 1 = 이 산출을 믿지 마라."""
    tool = _tool()
    assert tool.main(["--corpus", str(_corpus(tmp_path)), "--json"]) == 1
    measured = tool.measure(_corpus(tmp_path), tool.baseline())
    assert [m for m in tool.misses(measured) if m.startswith("floor   파데프리")]


def test_the_tool_is_green_on_a_corpus_that_matches_the_ledger(tmp_path, capsys):
    """빨간 자리만 있고 초록이 도달 불가하면 위 테스트는 항등식이다 -- 원장대로인 코퍼스를 지어
    종료 코드 0 이 실제로 나오는 것을 본다."""
    tool = _tool()
    lines = []
    for index, row in enumerate(tool.LEDGER):
        needed = max(row.df, MIN_DOCS if row.verdict == "등재" else 0)
        lines += [
            f"x{index}_{n},youtube_comment,x{index}_{n},comment,,,,,{row.term},,{{}}" for n in range(needed)
        ]
    path = tmp_path / "document.csv"
    path.write_text(CORPUS_HEADER + "\n".join(lines) + "\n", encoding="utf-8")
    assert tool.main(["--corpus", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["misses"] == []


def test_the_tool_reads_the_repo_csv_as_its_baseline_dictionary():
    """기준 사전이 원장의 표기를 하나도 갖지 않는다 -- 가지면 등재한 뒤 `new` 가 전부 0 이 되어
    "이 행이 무엇을 관측하는가"를 다시 물을 수 없다."""
    tool = _tool()
    aliases = {a for e in tool.baseline().entries for a in e["ko"] + e["latin"]}
    assert not aliases & {row.term for row in LEDGER}
    assert {a for e in csv_topics().entries for a in e["ko"] + e["latin"]} & {r.term for r in LISTED} == {
        row.term for row in LISTED
    }


def test_the_tool_is_wired_to_the_read_only_handoff_corpus():
    """기본 경로가 레포 안이면 26만 문서가 아니라 픽스처를 재고 그 수가 원장에 실린다."""
    tool = _tool()
    assert tool.CORPUS.name == "document.csv"
    assert "yt-handoff" in str(tool.CORPUS)
    with pytest.raises(FileNotFoundError):
        tool.measure(Path("/nonexistent/document.csv"), tool.baseline())


# ---------- 적재 전후로 무엇이 움직이는가 (포크 #58 의 첫 실측) ----------


@pytest.mark.postgres
def test_raising_the_aspect_dictionary_to_v3_does_not_move_the_run_stamp(needs_runtime_url: str):
    """포크 #58 은 `entity_lexicon` 의 번호표가 kind 를 안 가린다고 적는다. **주제 사전은 그 자리를
    밟지 않는다** -- `aspect_lexicon` 은 다른 표라 `versions.lexicon`(`aggregate/pipeline.py:149` 의
    `SELECT max(version) FROM entity_lexicon`)을 나눠 갖지 않는다. 코디네이터가 v3 를 켤 때 무엇이
    움직이고 무엇이 안 움직이는지가 이 테스트의 답이다.

    변이: `entity_lexicon` 에 v3 를 한 행 넣으면 (활성이 아니어도) 그 칸이 3 이 된다 -- #58 이 고칠
    자리가 실제로 무는 것을 여기서 본다.
    """
    from analysis.aggregate.pipeline import _versions
    from analysis.lexicon import load_aspects
    from analysis.retrieval import topics as topic_registry
    from cosmai.cli import main as cli
    from db.lexicon import insert_entities
    from tests.retrieval.test_topics import _connect

    class _Aggregator:
        version = "rules-test"

    stamp = lambda cur: _versions(cast(Any, _Aggregator()), cur, [])["lexicon"]  # noqa: E731

    with _connect(needs_runtime_url) as conn, conn.cursor() as cur:
        insert_entities(cur, [("brand", "라네즈", "라네즈", "normal", "oliveyoung", None)], 1)
        conn.commit()
        assert stamp(cur) == 1
        for version in (1, 3):
            argv = ["lexicon", "load", "--kind", "aspect", "--version", str(version)]
            assert cli([*argv, str(topic_registry.DICTIONARY_CSV), "--url", needs_runtime_url]) == 0
        assert (
            cli(["lexicon", "activate", "--kind", "aspect", "--version", "3", "--url", needs_runtime_url])
            == 0
        )
        conn.commit()
        assert load_aspects(conn, topic_registry.RULESET).version == 3
        assert topic_registry.load(conn).version == 3
        assert stamp(cur) == 1, "aspect v3 가 entity 의 번호표를 움직였다"
        # 변이: entity 쪽에 v3 가 한 행이라도 들어오면 (안 켜도) 그 칸이 움직인다 (#58).
        insert_entities(cur, [("brand", "라네즈", "라네즈", "normal", "oliveyoung", None)], 3, active=False)
        conn.commit()
        assert stamp(cur) == 3
