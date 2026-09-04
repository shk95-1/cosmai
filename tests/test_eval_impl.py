"""`cosmai eval <task> --impl <spec>`: the registered factory builds the implementation of that run (the
llm:<model> of #6)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from decimal import Decimal

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

    # The oracle stands in the place of a real implementation -- if load_implementations() of the cli plugs
    # the rules back on top of it, the rules run instead of the --split wiring under measurement (the same
    # place as harness_only in test_cli_eval.py).
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(registry, "IMPLEMENTATIONS", ())
        register("polarity", "oracle-v0", predict)
        yield
    unregister("polarity")
    # Where the next test gets back the registrations this file swapped out (#30).
    registry.load_implementations()


def test_the_llm_factory_is_wired_by_the_implementations_list_and_not_by_the_cli():
    """A unit adds only one line to IMPLEMENTATIONS -- #5 and #9 are working on the same cli.py."""
    registry.load_implementations()
    built = registry.build("polarity", "llm:claude-sonnet-5")
    assert built is not None and built.version == f"llm-claude-sonnet-5-{PROMPT_DATE}"


def test_building_does_not_open_a_connection_or_call_anything():
    """The factory builds only the name -- the first API call happens when predict is called (the offline
    suite proves it)."""
    registry.load_implementations()
    assert registry.build("polarity", "llm:claude-opus-5") is not None


def test_the_ollama_factory_is_wired_and_free_so_it_skips_the_split_guard():
    """#6/#21: ollama costs nothing -- is_paid has to be False so the forced --split does not apply."""
    registry.load_implementations()
    built = registry.build("polarity", "ollama:gemma4:latest")
    assert built is not None and built.version.startswith("llm-ollama-gemma4:latest-")
    assert registry.is_paid("polarity", "ollama:gemma4:latest") is False


def test_building_the_ollama_impl_does_not_open_a_connection_or_call_anything():
    """It builds only the name -- the ollama call goes out when predict is called (a suite with the network
    blocked proves it)."""
    registry.load_implementations()
    assert registry.build("polarity", "ollama:gemma4:latest") is not None


@pytest.mark.parametrize("spec", ["llm:", "ollama:", "ollama"])
def test_building_without_a_model_is_refused_by_the_factory_not_the_registry(spec: str):
    """#6: mutation deleting these two guard lines killed 0 tests -- looking only at the exception type, the
    guard can be deleted and still pass through the "no implementation factory" LookupError of
    registry.build, so the message demanding a model name has to be asserted as well."""
    registry.load_implementations()
    with pytest.raises(LookupError) as missing_model:
        registry.build("polarity", spec)
    assert "no implementation factory" not in str(missing_model.value)
    assert "needs a model" in str(missing_model.value)


def test_an_unknown_impl_stops_with_exit_code_2_before_anything_is_spent(capsys):
    assert main(["eval", "polarity", "--impl", "gpt:whatever"]) == 2
    assert "gpt:whatever" in capsys.readouterr().out


@pytest.mark.postgres
def test_a_split_limits_the_eval_sets_that_are_scored(labeled: str, oracle: None, capsys):
    """The holdout is not looked at during tuning -- it is not that the score is hidden but that it is not run
    at all."""
    assert main(["eval", "polarity", "--url", labeled, "--split", "tune"]) == 0
    out = capsys.readouterr().out
    assert "sun tune 200" in out
    assert "sun holdout 100" not in out and "p1 blind40" not in out


def test_the_adoption_bar_is_what_the_rule_actually_scored_not_the_contract_floor():
    """The contract floor is sun .77/.89 but the rules produced .870/.915 -- the replacement condition is the
    latter (issue #6)."""
    assert RULE_MEASURED["polarity"]["sun holdout 100"] == {"acc": Decimal(".870"), "P:불만": Decimal(".915")}
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
    """The scores of a tune-only run hold no holdout -- an empty tuple must not be read as 'replace'."""
    with pytest.raises(LookupError) as unusable:
        adoption_misses("polarity", {"sun holdout 100": {"acc": 0.99, "P:불만": 0.99}})
    assert "p1 blind40" in str(unusable.value)


def test_a_paid_impl_without_a_split_is_refused_before_the_holdout_goes_out(capsys):
    """The baseline table gives the holdout back first -- without --split the first call goes out on the blind
    set."""
    assert main(["eval", "polarity", "--impl", "llm:claude-sonnet-5"]) == 2
    assert "--split" in capsys.readouterr().out
