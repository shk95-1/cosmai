"""`cosmai analyze <stage>`: 계약이 이름 붙인 stage 만 있고, 그 각각이 실제 단계에 닿는다 (entrypoints.md).

돌아가는 단계의 행위는 tests/test_analyze_all.py 가 실물 Postgres 위에서 본다 — 여기는 배선만이다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

import analysis.pipeline
from cosmai.cli import STAGES, main


class _FakeConn:
    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *_: object) -> bool:
        return False


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_run_stage(conn: object, stage: str, **kwargs: Any) -> analysis.pipeline.StageOutcome:
        calls.append({"stage": stage, **kwargs})
        return analysis.pipeline.StageOutcome(stage, "ok", 7, {"metrics_need": 1})

    monkeypatch.setattr("cosmai.cli._connect", lambda url: _FakeConn())
    monkeypatch.setattr(analysis.pipeline, "run_stage", fake_run_stage)
    return calls


def test_the_stage_list_is_the_one_the_contract_names():
    assert STAGES == ("link", "polarity", "aggregate", "all")


@pytest.mark.parametrize("stage", STAGES)
def test_every_stage_reaches_the_pipeline_and_exits_zero(stage: str, recorded: list[dict[str, Any]]):
    assert main(["analyze", stage]) == 0
    assert recorded == [{"stage": stage, "since": None, "scope": None}]


def test_since_and_scope_are_handed_to_the_stage(recorded: list[dict[str, Any]]):
    assert main(["analyze", "all", "--since", "2026-03-01", "--scope", "선블록"]) == 0
    assert recorded == [{"stage": "all", "since": date(2026, 3, 1), "scope": "선블록"}]


def test_a_since_that_is_not_a_date_is_blocked_before_anything_runs(
    recorded: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
):
    assert main(["analyze", "all", "--since", "yesterday"]) == 2
    assert not recorded
    assert capsys.readouterr().out.strip()


def test_a_failed_stage_is_exit_one(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr("cosmai.cli._connect", lambda url: _FakeConn())
    monkeypatch.setattr(
        analysis.pipeline,
        "run_stage",
        lambda conn, stage, **kwargs: analysis.pipeline.StageOutcome(stage, "failed", None, {}, "link boom"),
    )
    assert main(["analyze", "all"]) == 1
    assert "failed:link boom" in capsys.readouterr().out


def test_a_stage_the_contract_does_not_have_is_refused_by_the_parser():
    with pytest.raises(SystemExit) as refused:
        main(["analyze", "extract"])
    assert refused.value.code == 2
