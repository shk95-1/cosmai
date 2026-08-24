"""ollama 배관. 무료·로컬이고 채택 판정에는 쓰지 않는다 — 재는 것은 같은 시그니처와 왕복뿐이다."""

from __future__ import annotations

import csv
import json
import re

import pytest

from analysis.lexicon import DISCOURSE_MARKERS, WISH_MARKERS
from analysis.polarity import GENERIC_RULESET, SUNCARE_RULESET
from analysis.polarity.fewshot import FEWSHOT_TAG, SHOTS
from analysis.polarity.llm import POLARITIES, version_for
from analysis.polarity.ollama import DEFAULT_OLLAMA_MODEL, OllamaPolarity, chat_payload, ollama_url
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
    """프롬프트가 Claude 경로와 갈라졌다 — 공유 PROMPT_DATE 를 주장하면 interfaces.md 가 거짓이 된다."""
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
    """블라인드는 이미 2회 닳았다 — 예시가 홀드아웃에서 새면 남은 1회가 튜닝 점수가 된다."""
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
