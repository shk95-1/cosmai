"""사전 판본을 다시 찍는 길 (`tool/show-lexicon-stamp`, 포크 #62).

계약(`contracts/interfaces.md` §검색 실측)이 적어 둔 사전 판본을 다시 확인하는 자리다 --
`tool/show-vector-stamp` 가 벡터 축에서 하는 일과 같고, 내는 문자열도 `retrieval eval` 이
행마다 싣는 그것과 **같아야 한다**. 두 벌이 되면 계약이 인용한 판본과 행이 적은 판본이 갈린다.
"""

from __future__ import annotations

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType

import pytest

from analysis.retrieval import topics
from tests.retrieval.conftest import install_topics

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tool" / "show-lexicon-stamp"


def loaded() -> ModuleType:
    """확장자가 없어 평범한 import 로는 안 들어온다 (`test_vector_floor.loaded` 와 같은 길)."""
    spec = spec_from_loader("show_lexicon_stamp", SourceFileLoader("show_lexicon_stamp", str(TOOL)))
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_tool_is_linted_like_the_rest():
    """확장자가 없는 파일은 ruff 가 기본으로 안 본다 -- 넣지 않으면 이 도구만 검사 밖에 산다 (포크 #61)."""
    assert TOOL.exists()
    assert f'"tool/{TOOL.name}"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_the_csv_stamp_is_the_string_the_dictionary_itself_gives(capsys):
    """도구가 판본 문자열을 따로 지으면 계약이 인용한 값과 평가 행의 값이 조용히 갈린다."""
    assert loaded().main(["--csv", str(topics.DICTIONARY_CSV)]) == 0
    printed = capsys.readouterr().out.strip()
    assert printed.startswith("ruleset=retrieval-topic · version=미적재")
    assert printed.endswith(f"fingerprint={_csv_dictionary().fingerprint}")


def _csv_dictionary() -> topics.Topics:
    from tests.retrieval.conftest import csv_topics

    return csv_topics(version=None)


def test_an_unreadable_source_is_blocked_not_failed(capsys, tmp_path):
    # 사전이 아직 없는 것은 실패가 아니라 아직 안 한 일이다 -- 벡터 저장소가 없는 것과 같은 자리다.
    assert loaded().main(["--csv", str(tmp_path / "없다.csv")]) == 2
    assert loaded().main(["--csv", str(topics.DICTIONARY_CSV), "--version", "3"]) == 2
    capsys.readouterr()


@pytest.mark.postgres
def test_the_stamp_of_the_active_version_comes_from_the_database(needs_runtime_url: str, capsys):
    from db.seed._common import connect

    with connect(needs_runtime_url) as conn:
        install_topics(conn, version=1)
        stamped = topics.load(conn).stamp
    assert loaded().main(["--url", needs_runtime_url]) == 0
    assert capsys.readouterr().out.strip() == stamped
    assert "version=1" in stamped


@pytest.mark.postgres
def test_two_versions_are_compared_on_the_axis_they_differ_on(needs_runtime_url: str, capsys):
    """`cosmai lexicon diff` 는 행 단위라 한 주제의 별칭이 늘면 행 하나로 보이고, 순서가 갈린 것은
    아예 안 보인다. 여섯 줄의 판본을 되짚은 것이 이 축의 대조다 (포크 #62)."""
    from db.lexicon import activate, insert_aspects
    from db.seed._common import connect
    from tests.retrieval.conftest import csv_rows

    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        insert_aspects(cur, csv_rows(), 1, active=False)
        wider = ("백탁", "generic", "", "허옇", False, topics.RULESET, 1, {"term_kind": "ko"})
        insert_aspects(cur, [*csv_rows(), wider], 2, active=False)
        activate(cur, "aspect", 2)
        conn.commit()
    assert loaded().main(["--version", "2", "--against", "1", "--url", needs_runtime_url]) == 0
    out = capsys.readouterr().out
    assert "version=2" in out and "version=1" in out
    assert "~ 백탁.ko" in out
    # 갈렸다는 것은 답이지 막힘이 아니다 -- 종료 코드는 그것으로 움직이지 않는다.
    assert loaded().main(["--version", "1", "--against", "1", "--url", needs_runtime_url]) == 0
    assert "차이 없다" in capsys.readouterr().out
