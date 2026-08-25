"""`cosmai eval --url X` 는 **모든** 예측자의 사전 커넥션을 X 로 보낸다.

#12 는 극성 예측자 한 자리를 고쳤고(`predictors.set_lexicon_url`), linker 계열은 자기 URL 을 따로
들고 있어 그 배선이 닿지 않았다 — `--check-baseline` 을 재던 실행이 운영 DB 의 entity_lexicon 을
읽었다. 같은 구멍이 세 번째 예측자에서 다시 나지 않도록, 여기서는 등록 목록(`registry.IMPLEMENTATIONS`
· `registry.TASKS`)을 돌면서 검사한다 — 새 예측자가 들어와도 손대지 않은 채로 걸린다.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest

from analysis import predictors, registry
from analysis.registry import LabeledRow
from cosmai.cli import main
from db import seed

PROD = "postgresql+psycopg://sentinel@prod/app"
ELSEWHERE = "postgresql+psycopg://elsewhere@test/fleet"


class Refused(Exception):
    """connect 를 세우지 않고 어느 URL 로 가려 했는지만 붙잡는다."""


@pytest.fixture
def default_implementations() -> Iterator[None]:
    """다른 파일이 지웠거나 갈아 끼운 등록을 되돌린다 — 검사 대상은 **기본** 등록이다."""
    registry.load_implementations()
    yield
    registry.load_implementations()


@pytest.fixture
def no_connection(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """사전 커넥션이 실제로 열리지 않게 잡아 두고, 겨냥한 URL 만 모은다."""
    seen: list[str] = []

    def refuse(url: str, **_: object) -> object:
        seen.append(url)
        raise Refused(url)

    monkeypatch.setattr("db.runtime.runtime_url", lambda: PROD)
    monkeypatch.setattr("db.seed._common.connect", refuse)
    return seen


def _rows() -> list[LabeledRow]:
    return [
        LabeledRow(
            task="brand_link",
            ref="p3:youtube/abc/브랜드",
            split="holdout",
            gold="OK",
            text="이 브랜드 좋아요",
            extra={},
        )
    ]


def _targets(seen: list[str]) -> dict[str, set[str]]:
    """등록된 예측자를 하나씩 태우고, 각자 사전을 어느 URL 에서 읽으려 했는지 모은다."""
    out: dict[str, set[str]] = {}
    for task in registry.TASKS:
        impl = registry.get(task)
        assert impl is not None, f"{task}: 기본 등록이 없다 — IMPLEMENTATIONS 가 이 task 를 꽂지 않았다"
        mark = len(seen)
        # 가짜 행이라 사전을 연 뒤에는 무엇으로 터지든 상관없다 — 재는 것은 커넥션의 목적지뿐이다.
        with contextlib.suppress(Exception):
            impl.predict(_rows())
        out[task] = set(seen[mark:])
    return out


# 오늘 사전을 읽는 예측자들. 여기 없는 task 가 커넥션을 열면 아래 두 검사가 그 이름을 대며 죽는다 —
# 새 예측자는 이 줄을 고치면서 자기 사전 접속이 어느 URL 로 가는지 한 번은 보게 된다.
READS_A_LEXICON = frozenset({"polarity", "wish_class", "brand_link"})


def test_every_registered_predictor_reads_its_lexicon_from_the_url_it_was_given(
    default_implementations: None, no_connection: list[str]
):
    """예측자마다 사전 접속을 스스로 여는데(Predictor 계약), 그 접속들이 같은 한 자리를 보고 가야 한다."""
    predictors.set_lexicon_url(ELSEWHERE)
    targets = _targets(no_connection)
    assert {task for task, urls in targets.items() if urls} == READS_A_LEXICON
    assert set().union(*targets.values()) == {ELSEWHERE}


def test_without_the_url_flag_the_lexicon_connection_still_falls_back_to_production(
    default_implementations: None, no_connection: list[str]
):
    """`--url` 없는 호출의 기본 동작은 그대로다 — 운영 폴백을 없앤 것이 아니라 --url 이 닿게 한 것이다."""
    predictors.set_lexicon_url(None)
    targets = _targets(no_connection)
    assert {task for task, urls in targets.items() if urls} == READS_A_LEXICON
    assert set().union(*targets.values()) == {PROD}


@pytest.mark.postgres
@pytest.mark.parametrize("task", registry.TASKS)
def test_eval_with_a_url_never_reaches_for_the_production_runtime_url(
    task: str, default_implementations: None, needs_runtime_url: str, monkeypatch: pytest.MonkeyPatch
):
    """실제 `cosmai eval` 한 번을 태워 본다 — 운영 폴백이 불리면 그 자리에서 죽는다."""
    seed.run_all(needs_runtime_url, only=("lexicon", "labeled"))
    monkeypatch.setattr(
        "db.runtime.runtime_url",
        lambda: (_ for _ in ()).throw(AssertionError(f"{task}: must not touch prod runtime_url")),
    )
    assert main(["eval", task, "--url", needs_runtime_url, "--split", "holdout"]) == 0
