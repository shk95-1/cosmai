"""`cosmai eval <task> --impl <spec>`: 등록된 팩터리가 그 실행의 구현체를 만든다 (#6 의 llm:<model>)."""

from __future__ import annotations

import importlib
from collections.abc import Iterator, Sequence

import pytest

from analysis import registry
from analysis.baselines import RULE_MEASURED, adoption_misses
from analysis.polarity.llm import PROMPT_DATE
from analysis.registry import LabeledRow, register, unregister
from cosmai.cli import main
from db import seed


@pytest.fixture
def labeled(needs_runtime_url: str) -> str:
    seed.run_all(needs_runtime_url, only=("labeled",))
    return needs_runtime_url


@pytest.fixture
def oracle() -> Iterator[None]:
    def predict(rows: Sequence[LabeledRow]) -> Sequence[str]:
        return [row.gold for row in rows]

    register("polarity", "oracle-v0", predict)
    yield
    unregister("polarity")
    # 등록 모듈은 import 캐시라 load_implementations() 가 register() 를 다시 돌려 주지 않는다 —
    # 이 파일이 지운 등록을 다음 테스트가 되찾을 방법은 그 모듈을 다시 실행하는 것뿐이다.
    importlib.reload(importlib.import_module("analysis.predictors"))


def test_the_llm_factory_is_wired_by_the_implementations_list_and_not_by_the_cli():
    """유닛은 IMPLEMENTATIONS 에 한 줄만 더한다 — #5·#9 가 같은 cli.py 를 만지고 있다."""
    registry.load_implementations()
    built = registry.build("polarity", "llm:claude-sonnet-5")
    assert built is not None and built.version == f"llm-claude-sonnet-5-{PROMPT_DATE}"


def test_building_does_not_open_a_connection_or_call_anything():
    """팩터리는 이름만 만든다 — 첫 API 호출은 predict 가 불릴 때다 (오프라인 스위트가 이걸 증명한다)."""
    registry.load_implementations()
    assert registry.build("polarity", "llm:claude-opus-5") is not None


def test_an_unknown_impl_stops_with_exit_code_2_before_anything_is_spent(capsys):
    assert main(["eval", "polarity", "--impl", "gpt:whatever"]) == 2
    assert "gpt:whatever" in capsys.readouterr().out


@pytest.mark.postgres
def test_a_split_limits_the_eval_sets_that_are_scored(labeled: str, oracle: None, capsys):
    """튜닝 중에는 홀드아웃을 보지 않는다 — 점수를 숨기는 것이 아니라 아예 돌리지 않는 것이다."""
    assert main(["eval", "polarity", "--url", labeled, "--split", "tune"]) == 0
    out = capsys.readouterr().out
    assert "sun tune 200" in out
    assert "sun holdout 100" not in out and "p1 blind40" not in out


def test_the_adoption_bar_is_what_the_rule_actually_scored_not_the_contract_floor():
    """계약 바닥은 sun .77/.89 지만 규칙은 .870/.915 를 냈다 — 교체 조건은 뒤쪽이다 (이슈 #6)."""
    assert RULE_MEASURED["polarity"]["sun holdout 100"] == {"acc": 0.870, "P:불만": 0.915}
    floor_only = {
        "sun holdout 100": {"acc": 0.80, "P:불만": 0.90},
        "p1 blind40": {"acc": 0.50, "P:불만": 0.70},
    }
    assert adoption_misses("polarity", floor_only) == (
        "sun holdout 100: acc 0.800 < rule 0.870",
        "sun holdout 100: P:불만 0.900 < rule 0.915",
    )
    beats = {
        "sun holdout 100": {"acc": 0.90, "P:불만": 0.95},
        "p1 blind40": {"acc": 0.50, "P:불만": 0.70},
    }
    assert adoption_misses("polarity", beats) == ()


def test_a_run_that_skipped_a_holdout_set_is_an_error_not_an_adoption():
    """튠만 돈 실행의 scores 에는 홀드아웃이 없다 — 빈 튜플을 '교체'로 읽으면 안 된다."""
    with pytest.raises(LookupError) as unusable:
        adoption_misses("polarity", {"sun holdout 100": {"acc": 0.99, "P:불만": 0.99}})
    assert "p1 blind40" in str(unusable.value)


def test_a_paid_impl_without_a_split_is_refused_before_the_holdout_goes_out(capsys):
    """기준선 표는 홀드아웃을 먼저 돌려준다 — --split 없이는 첫 호출이 블라인드 셋으로 나간다."""
    assert main(["eval", "polarity", "--impl", "llm:claude-sonnet-5"]) == 2
    assert "--split" in capsys.readouterr().out
