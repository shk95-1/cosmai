"""ollama 배관. 무료·로컬이고 채택 판정에는 쓰지 않는다 — 재는 것은 같은 시그니처와 왕복뿐이다."""

from __future__ import annotations

import re

import pytest

from analysis.lexicon import DISCOURSE_MARKERS, WISH_MARKERS
from analysis.polarity.llm import POLARITIES
from analysis.polarity.ollama import DEFAULT_OLLAMA_MODEL, OllamaPolarity, chat_payload, ollama_url
from analysis.polarity.prompt import LABEL_CRITERIA
from analysis.types import AspectLexicon, AspectPattern, Polarity

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
    assert SENTENCE in payload["messages"][1]["content"]
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
