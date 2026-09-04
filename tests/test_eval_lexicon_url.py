"""`cosmai eval --url X` sends the dictionary connection of **every** predictor to X.

#12 fixed one place, the polarity predictor (`predictors.set_lexicon_url`), and the linker family held URLs
of its own so that wiring never reached it -- a run measuring `--check-baseline` read the entity_lexicon of
the production DB. So that the same hole does not open again in a third predictor, this walks the
registration lists (`registry.IMPLEMENTATIONS` · `registry.TASKS`) and checks them -- a new predictor is
caught without touching anything.
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
    """It does not stand connect up and only catches which URL it meant to go to."""


@pytest.fixture
def default_implementations() -> Iterator[None]:
    """Restores registrations another file deleted or swapped -- what is checked is the **default**
    registration."""
    registry.load_implementations()
    yield
    registry.load_implementations()


@pytest.fixture
def no_connection(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Holds the dictionary connection from really opening and collects only the URLs it aimed at."""
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
    """Runs each registered predictor and collects which URL each tried to read its dictionary from."""
    out: dict[str, set[str]] = {}
    for task in registry.TASKS:
        impl = registry.get(task)
        assert impl is not None, f"{task}: 기본 등록이 없다 — IMPLEMENTATIONS 가 이 task 를 꽂지 않았다"
        mark = len(seen)
        # The rows are fake, so after the dictionary is opened it does not matter what it blows up on -- what
        # is measured is only the destination of the connection.
        with contextlib.suppress(Exception):
            impl.predict(_rows())
        out[task] = set(seen[mark:])
    return out


# The predictors that read a dictionary today. A task not here that opens a connection makes the two checks
# below die naming it -- a new predictor fixes this line and so sees once which URL its dictionary connection
# goes to.
READS_A_LEXICON = frozenset({"polarity", "wish_class", "brand_link"})


def test_every_registered_predictor_reads_its_lexicon_from_the_url_it_was_given(
    default_implementations: None, no_connection: list[str]
):
    """Each predictor opens its own dictionary connection (the Predictor contract), and those connections have
    to look at one and the same place."""
    predictors.set_lexicon_url(ELSEWHERE)
    targets = _targets(no_connection)
    assert {task for task, urls in targets.items() if urls} == READS_A_LEXICON
    assert set().union(*targets.values()) == {ELSEWHERE}


def test_without_the_url_flag_the_lexicon_connection_still_falls_back_to_production(
    default_implementations: None, no_connection: list[str]
):
    """The default behaviour of a call without `--url` is unchanged -- the production fallback was not
    removed; --url was made to reach it."""
    predictors.set_lexicon_url(None)
    targets = _targets(no_connection)
    assert {task for task, urls in targets.items() if urls} == READS_A_LEXICON
    assert set().union(*targets.values()) == {PROD}


@pytest.mark.postgres
@pytest.mark.parametrize("task", registry.TASKS)
def test_eval_with_a_url_never_reaches_for_the_production_runtime_url(
    task: str, default_implementations: None, needs_runtime_url: str, monkeypatch: pytest.MonkeyPatch
):
    """It runs one real `cosmai eval` -- if the production fallback is called it dies right there."""
    seed.run_all(needs_runtime_url, only=("lexicon", "labeled"))
    monkeypatch.setattr(
        "db.runtime.runtime_url",
        lambda: (_ for _ in ()).throw(AssertionError(f"{task}: must not touch prod runtime_url")),
    )
    assert main(["eval", task, "--url", needs_runtime_url, "--split", "holdout"]) == 0
