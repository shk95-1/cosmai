"""`cosmai analyze <stage>`: 계약이 이름 붙인 stage 만 있고, 그 각각이 실제 단계에 닿는다 (entrypoints.md).

돌아가는 단계의 행위는 tests/test_analyze_all.py 가 실물 Postgres 위에서 본다 — 여기는 배선만이다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import pytest

import analysis.pipeline
import analysis.polarity.ownership as ownership
from analysis import registry
from analysis.polarity import SUNCARE_CATEGORY
from analysis.polarity.ownership import ALWAYS, Owner
from cosmai.cli import STAGES, main

# OWNERS is suspended empty (#242): these tests patch it back in locally to prove the CLI wiring around a
# registered scope, independent of the shipped table's current (empty) state.
_SUNBLOCK_OWNED = {SUNCARE_CATEGORY: Owner("stub-v9", ALWAYS)}


@pytest.fixture
def sunblock_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ownership, "OWNERS", _SUNBLOCK_OWNED)


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
    assert recorded == [{"stage": stage, "since": None, "scope": None, "missing": False, "polarity": None}]


def test_since_and_scope_are_handed_to_the_stage(recorded: list[dict[str, Any]]):
    assert main(["analyze", "all", "--since", "2026-03-01", "--scope", "선블록"]) == 0
    assert recorded == [
        {"stage": "all", "since": date(2026, 3, 1), "scope": "선블록", "missing": False, "polarity": None}
    ]


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


class _StubPolarity:
    version = "stub-v9"

    def classify(self, sentence: str, rating: Any, category: Any, aspects: Any) -> Any: ...
    def classify_many(self, items: Any, aspects: Any) -> Any: ...


@pytest.fixture
def registered(monkeypatch: pytest.MonkeyPatch) -> _StubPolarity:
    """--impl 은 eval 이 쓰는 그 레지스트리·그 스펙 문법을 지난다 — 여기서 보는 것은 그 통과 여부다."""
    stub = _StubPolarity()

    @contextmanager
    def open_stub(spec: str) -> Iterator[_StubPolarity]:
        opened.append(spec)
        yield stub

    opened: list[str] = []
    monkeypatch.setattr(registry, "load_implementations", lambda: None)
    monkeypatch.setattr(registry, "open_classifier", lambda task, spec: open_stub(f"{task}:{spec}"))
    return stub


def test_impl_hands_the_registered_classifier_to_the_stage(
    recorded: list[dict[str, Any]], registered: _StubPolarity, sunblock_owned: None
):
    """남의 scope(선블록의 주인은 gemma4 다)를 지정한 실행도 여기서 막지 않는다 — 그 거절은 단계의
    몫이고 entrypoints.md 는 그것을 failed run + 종료 코드 1 로 약속한다."""
    # OWNERS is suspended empty (#242): `sunblock_owned` patches a scope back in to exercise this wiring.
    assert main(["analyze", "polarity", "--impl", "ollama:gemma4:latest", "--scope", SUNCARE_CATEGORY]) == 0
    assert recorded == [
        {
            "stage": "polarity",
            "since": None,
            "scope": SUNCARE_CATEGORY,
            "missing": False,
            "polarity": registered,
        }
    ]


def test_missing_reaches_the_stage_as_the_cron_will_type_it(
    recorded: list[dict[str, Any]], registered: _StubPolarity, sunblock_owned: None
):
    """`--missing` 을 판정하는 곳은 단계다(소유가 없으면 거절) — CLI 는 그것을 나르기만 한다."""
    # OWNERS is suspended empty (#242): `sunblock_owned` patches a scope back in to exercise this wiring.
    argv = ["analyze", "polarity", "--impl", "ollama:gemma4:latest", "--scope", SUNCARE_CATEGORY, "--missing"]
    assert main(argv) == 0
    assert recorded == [
        {
            "stage": "polarity",
            "since": None,
            "scope": SUNCARE_CATEGORY,
            "missing": True,
            "polarity": registered,
        }
    ]


def test_a_free_impl_without_a_scope_is_refused_too(
    recorded: list[dict[str, Any]], registered: _StubPolarity, capsys: pytest.CaptureFixture[str]
):
    """무료라서 위험이 없는 것이 아니다: 스코프 없는 gemma4 한 줄이 규칙 모집단 전량을 다시 라벨한다
    (GPU 수십 시간). 기준은 유료 여부가 아니라 '규칙이 아닌 구현'이다."""
    assert main(["analyze", "polarity", "--impl", "ollama:gemma4:latest"]) == 2
    assert not recorded
    assert "--scope" in capsys.readouterr().out


def test_an_impl_on_a_scope_with_no_owner_is_refused_before_the_pass_starts(
    recorded: list[dict[str, Any]], registered: _StubPolarity, capsys: pytest.CaptureFixture[str]
):
    """주인 없는 scope 는 규칙이 매일 05:00 에 다시 라벨한다 — 등록 없이 돌면 성공하고도 다음 새벽에
    사라진다. 주인 등록이 패스보다 먼저라는 순서를 여기서 강제한다 (analysis/polarity/ownership.py)."""
    assert main(["analyze", "polarity", "--impl", "ollama:gemma4:latest", "--scope", "미등록카테고리"]) == 2
    assert not recorded
    assert "ownership.py" in capsys.readouterr().out


def test_the_shipped_table_now_refuses_sunblock_too_while_owners_is_suspended(
    recorded: list[dict[str, Any]], registered: _StubPolarity, capsys: pytest.CaptureFixture[str]
):
    """The consequence of #242, intended: with OWNERS suspended empty even the sunblock scope (once the
    owner's own) is refused the same way as any other unregistered category, until re-registration."""
    assert main(["analyze", "polarity", "--impl", "ollama:gemma4:latest", "--scope", SUNCARE_CATEGORY]) == 2
    assert not recorded
    assert "ownership.py" in capsys.readouterr().out


def test_no_impl_still_leaves_the_rule_in_place(recorded: list[dict[str, Any]]):
    assert main(["analyze", "polarity"]) == 0
    assert recorded[0]["polarity"] is None


def test_an_impl_the_registry_does_not_know_is_blocked_before_the_stage_runs(
    recorded: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(registry, "load_implementations", lambda: None)
    assert main(["analyze", "polarity", "--impl", "nope:x"]) == 2
    assert not recorded
    assert "no stage classifier" in capsys.readouterr().out


def test_a_paid_impl_without_a_scope_is_refused_before_a_single_call_goes_out(
    recorded: list[dict[str, Any]],
    registered: _StubPolarity,
    sunblock_owned: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """eval 은 --split 으로 막는다. analyze 에는 split 이 없고 기본이 전량이라 --scope 가 그 자리다."""
    # OWNERS is suspended empty (#242): `sunblock_owned` patches a scope back in to exercise this wiring.
    monkeypatch.setattr(registry, "is_paid", lambda task, spec: True)
    assert main(["analyze", "polarity", "--impl", "llm:claude-sonnet-5"]) == 2
    assert not recorded
    assert "spends money" in capsys.readouterr().out
    assert main(["analyze", "polarity", "--impl", "llm:claude-sonnet-5", "--scope", SUNCARE_CATEGORY]) == 0
    assert recorded == [
        {
            "stage": "polarity",
            "since": None,
            "scope": SUNCARE_CATEGORY,
            "missing": False,
            "polarity": registered,
        }
    ]
