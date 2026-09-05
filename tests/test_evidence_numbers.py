"""Re-measures every time the fixture numbers the contract's §Evidence quotes and compares them (fork #6).

숫자를 계약에 적고 재는 길을 남기지 않으면, 픽스처가 자라는 순간 그 숫자는 조용히 거짓이 된다. #41 이
`tool/compare-ydc-sensitivity` 로 연 자리를 이 파일이 `tool/measure-evidence-fixture` 로 잇는다.

Three sets are compared in one place.
  (1) `tool/measure-evidence-fixture --json` -- the value re-measured from the corpus CSVs by the manifest
  rules
  (2) the numbers embedded in the sentences of `contracts/interfaces.md` §Evidence
  (3) the output `analysis/evidence/pipeline.py` actually produced from the DB
When (1) and (3) part, the tool has stopped being a copy of the pipeline; when (1) and (2) part, the contract
is stale.
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
    start = body.index("## Evidence (the consumer speech that holds up a verdict cell")
    return body[start : body.index("\n## ", start)]


def test_the_tool_still_runs_and_answers_in_the_shape_the_test_reads():
    got = measured()
    assert set(got) == {"gates", "retrieval"}
    assert got["gates"]["rows"] > 0 and got["retrieval"]["queries"] > 0


@pytest.mark.parametrize(
    ("sentence", "key", "section"),
    [
        ("Of the fixture's\n     719 candidate comments, **21** are dropped here", "comments", "gates"),
        ("(41 of 1216 candidate pairs)", "candidates", "gates"),
        ("96 fixture cells that carry evidence, **71** have a tie", "quoted_cells", "gates"),
        ("**57** of the 251 rows pick a different document", "rows", "gates"),
        ("population comments 2605 = 2646 chunks", "comments", "retrieval"),
        ("the 63 topic aliases", "queries", "retrieval"),
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
        (719, "comments"),
        (21, "creator_comments"),
        (1216, "candidates"),
        (41, "creator_pairs"),
        (96, "quoted_cells"),
        (71, "tied_cells"),
        (251, "rows"),
    ):
        assert gates[name] == value, f"{name}: the contract says {value}, the tool says {gates[name]}"
        assert str(value) in body, f"{name}={value} is not in the contract's §Evidence"


def test_every_number_the_retrieval_table_cites_is_the_number_the_tool_measures():
    """`.604` 도 `77.5%` 도 이 자리에서 다시 나온다 -- 재현 경로 없는 실측은 계약에 두지 않는다."""
    found = measured()["retrieval"]
    table = contract()
    assert f"P@10 **{found['p_at_10']:.3f}".replace("0.", ".") in table
    assert f"ceiling **{found['p_at_10_ceiling']:.3f}".replace("0.", ".") in table
    assert f"MRR@10 {found['mrr_at_10']:.3f}".replace("0.", ".") in table
    assert f"Hit@10 {found['hit_at_10']}%" in table
    assert (
        f"**{found['evidence_in_top10']}/{found['evidence_rows']} = {found['evidence_in_top10_pct']}%**"
        in table
    )
    assert f"comments {found['comments']} = {found['chunks']} chunks" in table
    assert f"({found['comments_split']} of the fixture's {found['comments']} population comments)" in table


def test_the_table_says_it_is_not_the_same_ruler_as_the_all_source_measurement():
    """Copied without its ceiling it sits beside the all-source `.864` and reads as "BM25 is weak"."""
    table = contract()
    assert "not the same footing as §Retrieval measurements" in table
    assert "ceiling" in table
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
