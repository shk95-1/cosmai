"""계약 §근거 가 인용하는 픽스처 수치를 매번 다시 재서 맞댄다 (포크 #6).

숫자를 계약에 적고 재는 길을 남기지 않으면, 픽스처가 자라는 순간 그 숫자는 조용히 거짓이 된다. #41 이
`tool/compare-ydc-sensitivity` 로 연 자리를 이 파일이 `tool/measure-evidence-fixture` 로 잇는다.

세 벌을 한자리에서 맞댄다.
  ① `tool/measure-evidence-fixture --json` -- 코퍼스 CSV 에서 매니페스트 규칙으로 다시 잰 값
  ② `contracts/interfaces.md` §근거 의 문장에 박힌 수
  ③ `analysis/evidence/pipeline.py` 가 DB 에서 실제로 낸 산출
①과 ③이 갈리면 도구가 파이프라인의 사본이 아니게 된 것이고, ①과 ②가 갈리면 계약이 낡은 것이다.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

from analysis.evidence.pipeline import build
from analysis.judge.pipeline import run as judge_run
from db.seed._common import connect
from tests.test_evidence_pipeline import judged  # noqa: F401  -- 같은 스키마 준비를 쓴다

ROOT = Path(__file__).resolve().parents[1]
INTERFACES = ROOT / "contracts" / "interfaces.md"
TOOL = ROOT / "tool" / "measure-evidence-fixture"


@lru_cache(maxsize=1)
def measured() -> dict[str, dict[str, float]]:
    """도구를 **세션에 한 번만** 부른다 -- Kiwi 를 얹고 색인을 세우는 일이라 부를 때마다 몇 초다."""
    done = subprocess.run(
        [sys.executable, str(TOOL), "--json"], capture_output=True, text=True, cwd=ROOT, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@lru_cache(maxsize=1)
def contract() -> str:
    body = INTERFACES.read_text(encoding="utf-8")
    start = body.index("## 근거 (판정 셀을 받치는 소비자 발화")
    return body[start : body.index("\n## ", start)]


def test_the_tool_still_runs_and_answers_in_the_shape_the_test_reads():
    got = measured()
    assert set(got) == {"gates", "retrieval"}
    assert got["gates"]["rows"] > 0 and got["retrieval"]["queries"] > 0


@pytest.mark.parametrize(
    ("sentence", "key", "section"),
    [
        ("후보 댓글\n     147건 중 **14건**", "comments", "gates"),
        ("(후보 쌍으로는 281 중 32)", "candidates", "gates"),
        ("근거가 선 픽스처 46셀 중 **23셀**에 동점이", "quoted_cells", "gates"),
        ("102행 중 **24행**에서 고르는 문서가 달라진다", "rows", "gates"),
        ("모집단 댓글 418건 = 청크 439개", "comments", "retrieval"),
        ("주제 별칭 61개", "queries", "retrieval"),
    ],
)
def test_the_contract_still_says_what_the_fixture_measures(sentence: str, key: str, section: str):
    """문장이 통째로 남아 있는지부터 본다 -- 숫자만 고치고 문장을 지우면 이 표가 눈을 감는다."""
    assert sentence in contract(), sentence
    assert measured()[section][key] > 0


def test_every_number_the_gates_paragraph_cites_is_the_number_the_tool_counts():
    gates = measured()["gates"]
    body = contract()
    for value, name in (
        (147, "comments"),
        (14, "creator_comments"),
        (281, "candidates"),
        (32, "creator_pairs"),
        (46, "quoted_cells"),
        (23, "tied_cells"),
        (102, "rows"),
    ):
        assert gates[name] == value, f"{name}: 계약은 {value}, 도구는 {gates[name]}"
        assert str(value) in body, f"{name}={value} 가 계약 §근거 에 없다"


def test_every_number_the_retrieval_table_cites_is_the_number_the_tool_measures():
    """`.604` 도 `77.5%` 도 이 자리에서 다시 나온다 -- 재현 경로 없는 실측은 계약에 두지 않는다."""
    found = measured()["retrieval"]
    table = contract()
    assert f"P@10 **{found['p_at_10']:.3f}".replace("0.", ".") in table
    assert f"천장 **{found['p_at_10_ceiling']:.3f}".replace("0.", ".") in table
    assert f"MRR@10 {found['mrr_at_10']:.3f}".replace("0.", ".") in table
    assert f"Hit@10 {found['hit_at_10']}%" in table
    assert (
        f"**{found['evidence_in_top10']}/{found['evidence_rows']} = {found['evidence_in_top10_pct']}%**"
        in table
    )
    assert f"댓글 {found['comments']}건 = 청크 {found['chunks']}개" in table
    assert f"418건 중 {found['comments_split']}건" in table


def test_the_table_says_it_is_not_the_same_ruler_as_the_all_source_measurement():
    """천장 없이 옮겨 적으면 전 소스 `.864` 와 나란히 놓여 "BM25 가 약하다"로 읽힌다."""
    table = contract()
    assert "§검색 실측 과 같은 자가 아니다" in table
    assert "천장" in table
    assert "#11 의 기본 엔진 판단에 입력으로 쓰지 않는다" in table


@pytest.mark.postgres
def test_the_tool_counts_what_the_database_pipeline_actually_produced(judged: str):  # noqa: F811
    """도구는 CSV 를, 파이프라인은 SQL 을 읽는다 -- 두 벌이 같은 수를 내야 도구가 사본으로 산다."""
    with connect(judged) as conn:
        judge_run(conn)
        made = build(conn)
    gates = measured()["gates"]
    assert gates["candidates"] == len(made.candidates)
    assert gates["rows"] == len(made.rows)
    assert gates["quoted_cells"] == len({(row.topic_key, row.quarter) for row in made.rows})
    assert gates["creator_pairs"] == sum(
        1 for c in made.candidates if c.author_channel_hash and _is_creator(c)
    )


def _is_creator(candidate) -> bool:
    from analysis.evidence import is_creator

    return is_creator(candidate)


def test_the_contract_points_at_the_tool_that_re_measures_it():
    assert "tool/measure-evidence-fixture" in contract()
    assert re.search(r"tests/test_evidence_numbers\.py", contract())
