"""민감도 명령은 답만 내고 아무것도 쓰지 않는다 (포크 #41).

`tests/test_sensitivity_golden.py` 가 값을 지키고 `tests/test_sensitivity_rules.py` 가 규칙을 진다면,
여기는 **경계**를 묻는다: 무엇이 있어야 이 명령이 서는가(막힘), 답이 흔들릴 때 종료 코드가 무엇인가,
그리고 돌고 난 뒤 저장된 표가 그대로인가. 마지막 것이 이 단계의 계약이다 -- 반사실 모집단에는 022 의
어휘에도 `analysis_run` 에도 자리가 없으므로, 쓰지 않는 것이 이 명령의 성질이지 규율이 아니다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import create_engine, text

from analysis.retrieval import topics as topic_registry
from analysis.sensitivity.pipeline import NoBaseline, build
from analysis.trend.pipeline import NoPopulation
from analysis.trend.pipeline import run as run_quarter
from cosmai.cli import main
from db import corpus, seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "trend_sample"
VIEWS = (
    ROOT / "db" / "views" / "metrics_topic_quarter_violation.sql",
    ROOT / "db" / "views" / "topic_quarter_judgement_violation.sql",
)
OWNER = text("SET ROLE needs_owner")
WRITTEN = ("metrics_topic_quarter", "topic_quarter_judgement", "analysis_run", "corpus_document")


def _install_registry(url: str) -> None:
    where = ["--kind", "aspect", "--version", "1", "--url", url]
    assert main(["lexicon", "load", *where, str(topic_registry.DICTIONARY_CSV)]) == 0
    assert main(["lexicon", "activate", *where]) == 0


@pytest.fixture
def empty(needs_schema: str, needs_runtime_url: str, _schema_name: str) -> str:
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            conn.execute(OWNER)
            for view in VIEWS:
                conn.exec_driver_sql(view.read_text(encoding="utf-8").replace("needs.", f'"{_schema_name}".'))
    finally:
        engine.dispose()
    return needs_runtime_url


@pytest.fixture
def loaded(empty: str) -> str:
    seed.run_all(empty, only=("panel",))
    _install_registry(empty)
    with connect(empty) as conn:
        corpus.load(conn, FIXTURE / "corpus")
    return empty


@pytest.fixture
def measured(loaded: str) -> str:
    with connect(loaded) as conn:
        run_quarter(conn)
    return loaded


def _fingerprint(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """저장된 표의 지문. 행 수만 세면 같은 수의 다른 행으로 바뀐 것을 못 본다."""
    found: dict[str, Any] = {}
    with conn.cursor() as cur:
        for table in WRITTEN:
            cur.execute(f"SELECT count(*), md5(string_agg(t::text, '|' ORDER BY t::text)) FROM {table} t")  # noqa: S608
            found[table] = cur.fetchone()
    return found


def test_without_an_active_roster_the_command_is_blocked_and_not_failed(empty: str):
    with connect(empty) as conn, pytest.raises(NoPopulation):
        build(conn)
    assert main(["trend", "sensitivity", "--url", empty]) == 2


def test_without_a_quarter_run_there_is_no_conclusion_to_be_sensitive_about(loaded: str):
    with connect(loaded) as conn, pytest.raises(NoBaseline) as blocked:
        build(conn)
    assert "cosmai trend quarter" in str(blocked.value)
    assert main(["trend", "sensitivity", "--url", loaded]) == 2


def test_the_command_leaves_every_stored_table_exactly_as_it_found_it(measured: str):
    with connect(measured) as conn:
        before = _fingerprint(conn)
    assert main(["trend", "sensitivity", "--url", measured]) in (0, 1)
    with connect(measured) as conn:
        assert _fingerprint(conn) == before


def test_a_conclusion_that_moves_is_reported_as_partial_and_not_as_ok(measured: str, capsys):
    """이 표본에서는 광고·협찬 영상을 빼면 유형이 세 셀에서 바뀐다 -- "흔들린다"를 0 으로 내면
    "안 흔들린다"와 구별되지 않는다."""
    assert main(["trend", "sensitivity", "--url", measured]) == 1
    printed = capsys.readouterr().out
    assert "trend sensitivity run=" in printed
    assert "ad_flips=3" in printed
    with connect(measured) as conn:
        built = build(conn)
    assert built.status == "partial"
    assert built.flipped_cells == 3
    assert built.violations == ()


def test_the_answer_names_the_run_and_the_snapshot_it_is_about(measured: str):
    with connect(measured) as conn:
        built = build(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT run_id FROM analysis_run ORDER BY run_id")
            assert [row[0] for row in cur.fetchall()] == [built.run_id]
    assert f"run={built.run_id}" in built.note
    assert f"snapshot={built.snapshot_id}" in built.note
