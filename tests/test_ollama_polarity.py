"""ollama plumbing. Free, local, and not used to decide adoption — what is measured is the same signature and
the round trip, nothing more."""

from __future__ import annotations

import csv
import json
import re
import urllib.error
import urllib.request
from typing import Any

import pytest

from analysis.lexicon import DISCOURSE_MARKERS, WISH_MARKERS
from analysis.polarity import GENERIC_RULESET, SUNCARE_RULESET
from analysis.polarity.fewshot import FEWSHOT_TAG, SHOTS
from analysis.polarity.llm import POLARITIES, version_for
from analysis.polarity.ollama import (
    DEFAULT_OLLAMA_MODEL,
    OLLAMA_URL_KEY,
    OllamaPolarity,
    chat_payload,
    ollama_url,
)
from analysis.polarity.prompt import LABEL_CRITERIA
from analysis.types import AspectLexicon, AspectPattern, Polarity
from db.seed._common import EVAL_DIR

NUM_SHOTS = len(SHOTS[SUNCARE_RULESET])

SUN = AspectLexicon(
    version=1,
    ruleset="suncare-v2.2",
    patterns=(
        AspectPattern(
            aspect="끈적유분",
            scope="category",
            category="선블록",
            pattern=re.compile("끈적"),
            is_neutral_noun=False,
            priority=0,
            ruleset="suncare-v2.2",
        ),
    ),
    discourse_marker_re=re.compile(DISCOURSE_MARKERS),
    wish_marker_re=re.compile(WISH_MARKERS),
)
SENTENCE = "끈적임이 너무 심해서 다시는 안 살 것 같아요"


def test_the_default_endpoint_is_the_local_one_and_the_variable_overrides_it(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    assert ollama_url() == "http://localhost:11434"
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:21434")
    assert ollama_url() == "http://127.0.0.1:21434"


def test_the_payload_carries_the_same_prompt_and_the_same_json_schema_as_the_claude_call():
    payload = chat_payload(DEFAULT_OLLAMA_MODEL, SENTENCE, 1.0, "선블록", SUN)
    assert payload["model"] == DEFAULT_OLLAMA_MODEL and payload["stream"] is False
    assert LABEL_CRITERIA in payload["messages"][0]["content"]
    assert SENTENCE in payload["messages"][-1]["content"]
    assert payload["format"]["properties"]["polarity"]["enum"] == list(POLARITIES)


def test_it_is_the_contract_protocol_and_says_so_in_its_version():
    found: Polarity = OllamaPolarity()
    assert found.version.startswith("llm-ollama-")
    assert re.match(r"^llm-.+-\d{8}$", found.version)


@pytest.mark.local_llm
def test_a_round_trip_through_the_local_model_comes_back_as_one_of_the_three_labels():
    found = OllamaPolarity().classify(SENTENCE, 1.0, "선블록", SUN)
    assert found.polarity in POLARITIES
    assert found.aspect in (None, "끈적유분")


def test_the_payload_turns_gemma_thinking_off():
    assert chat_payload(DEFAULT_OLLAMA_MODEL, SENTENCE, 1.0, "선블록", SUN).get("think") is False


def test_the_ollama_version_carries_its_own_prompt_edition_not_the_shared_one():
    """The prompt has diverged from the Claude path — claiming a shared PROMPT_DATE would make interfaces.md
    false."""
    found = OllamaPolarity().version
    assert re.match(r"^rule-v\d+\.\d+$|^llm-.+-\d{8}$", found)  # contracts/versioning.md:3
    assert FEWSHOT_TAG in found
    assert found != version_for(f"ollama-{DEFAULT_OLLAMA_MODEL}")


def test_the_shots_are_prior_turns_and_the_sentence_under_test_is_still_the_last_one():
    payload = chat_payload(DEFAULT_OLLAMA_MODEL, SENTENCE, 1.0, "선블록", SUN)
    messages = payload["messages"]
    assert [m["role"] for m in messages] == ["system"] + ["user", "assistant"] * NUM_SHOTS + ["user"]
    assert SENTENCE in messages[-1]["content"]
    for answer in messages[2::2]:
        assert json.loads(answer["content"])["polarity"] in POLARITIES


def test_every_shot_is_a_tune_row_and_no_shot_touches_a_holdout_sentence():
    """The blind has already been spent twice — an example leaking into the holdout turns the one that is left
    into a tuning score."""
    holdout = {
        row["sentence"]
        for name in ("suncare_holdout100.csv", "crosscat_blind40.csv")
        for row in csv.DictReader((EVAL_DIR / "polarity" / name).open(encoding="utf-8"))
    }
    tune = {
        name: {
            row["sentence"] for row in csv.DictReader((EVAL_DIR / "polarity" / name).open(encoding="utf-8"))
        }
        for name in ("suncare_tune200.csv", "crosscat_60.csv")
    }
    for ruleset, source in ((SUNCARE_RULESET, "suncare_tune200.csv"), (GENERIC_RULESET, "crosscat_60.csv")):
        for shot in SHOTS[ruleset]:
            assert shot.sentence in tune[source], f"{ruleset}: {shot.sentence!r} is not a {source} row"
            assert shot.sentence not in holdout


# --- start probe (#32): fail loudly when it cannot be reached -----------------------------------
# The suite cannot open a socket (_no_network in tests/conftest.py) — what is measured here is what the probe
# reads and decides on instead of a round trip, and the real round trip is done by the test above marked
# `local_llm`.
PROBE_URL = "http://127.0.0.1:21434"


class _Answer:
    def __init__(self, models: list[str]) -> None:
        self.body = json.dumps({"models": [{"model": name} for name in models]}).encode()

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def _serving(monkeypatch: pytest.MonkeyPatch, models: list[str]) -> list[str]:
    """An ollama whose `/api/tags` answers with these models. It gives back the URL the probe called."""
    called: list[str] = []

    def urlopen(request: Any, *args: Any, **kwargs: Any) -> _Answer:
        called.append(request.full_url)
        return _Answer(models)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return called


def _refusing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An address nobody listens on. The same URLError (= OSError) a container with broken wiring gives."""

    def urlopen(request: Any, *args: Any, **kwargs: Any) -> None:
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


def test_the_probe_asks_the_tags_endpoint_of_the_address_the_classifier_will_call(
    monkeypatch: pytest.MonkeyPatch,
):
    """If the probe looks at a different address, a probe that passed guarantees nothing."""
    monkeypatch.setenv(OLLAMA_URL_KEY, PROBE_URL)
    called = _serving(monkeypatch, [DEFAULT_OLLAMA_MODEL])
    OllamaPolarity().preflight()  # coming back quietly is the pass
    assert called == [f"{PROBE_URL}/api/tags"]


def test_the_probe_refuses_when_the_address_answers_but_has_no_such_model(monkeypatch: pytest.MonkeyPatch):
    """An open port does not mean the model is there — without this branch it is known at the first batch."""
    _serving(monkeypatch, ["something-else:latest"])
    with pytest.raises(LookupError) as refused:
        OllamaPolarity(base_url=PROBE_URL).preflight()
    assert DEFAULT_OLLAMA_MODEL in str(refused.value)
    assert "something-else:latest" in str(refused.value), "가진 모델을 안 적으면 오타를 못 본다"


def test_the_probe_names_the_address_and_the_knob_when_nothing_answers(monkeypatch: pytest.MonkeyPatch):
    """The one line that stays in the cron mail: where it dialled, and the name of the knob that changes that
    address, have to be there together.
    The type is fixed as well — it has to be LookupError for the stage to catch it and close the run as failed
    (analysis/pipeline.py)."""
    _refusing(monkeypatch)
    with pytest.raises(LookupError) as refused:
        OllamaPolarity(base_url=PROBE_URL).preflight()
    assert PROBE_URL in str(refused.value) and OLLAMA_URL_KEY in str(refused.value)
