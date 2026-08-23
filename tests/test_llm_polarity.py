"""LLMPolarity: 프롬프트·구조화 출력·재시도·배치·원장. 진짜 Anthropic 호출은 여기 없다 (가짜 클라이언트)."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from analysis.lexicon import DISCOURSE_MARKERS, WISH_MARKERS
from analysis.polarity import RulePolarity
from analysis.polarity.llm import DEFAULT_MODEL, PROMPT_DATE, LLMPolarity, version_for
from analysis.polarity.pricing import BudgetExceeded, Usage, UsageLedger
from analysis.polarity.prompt import LABEL_CRITERIA, system_prompt, user_prompt
from analysis.types import AspectLexicon, AspectPattern, Polarity, PolarityRequest
from db.seed._common import connect

FORMATS = Path(__file__).resolve().parents[1] / "contracts" / "formats.md"


def _pattern(aspect: str, category: str, ruleset: str = "suncare-v2.2") -> AspectPattern:
    return AspectPattern(
        aspect=aspect,
        scope="category" if category else "generic",
        category=category,
        pattern=re.compile(aspect),
        is_neutral_noun=False,
        priority=0,
        ruleset=ruleset,
    )


SUN = AspectLexicon(
    version=1,
    ruleset="suncare-v2.2",
    patterns=(
        _pattern("끈적유분", "선블록"),
        _pattern("백탁", "선블록"),
        _pattern("배송포장", ""),
    ),
    discourse_marker_re=re.compile(DISCOURSE_MARKERS),
    wish_marker_re=re.compile(WISH_MARKERS),
)
SENTENCE = "끈적임이 너무 심해서 다시는 안 살 것 같아요"
USAGE = SimpleNamespace(
    input_tokens=100, output_tokens=50, cache_read_input_tokens=0, cache_creation_input_tokens=0
)


def _answer(polarity: str, aspect: str | None = "끈적유분", reason: str = "부정 경험") -> str:
    return json.dumps({"aspect": aspect, "polarity": polarity, "reason": reason}, ensure_ascii=False)


def _message(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], usage=USAGE)


class FakeBatches:
    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.submitted: list[Any] = []

    def create(self, *, requests: list[Any]) -> SimpleNamespace:
        self.submitted.append(requests)
        return SimpleNamespace(id="msgbatch_fake")

    def retrieve(self, batch_id: str) -> SimpleNamespace:
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def results(self, batch_id: str) -> Any:
        # 결과는 아무 순서로나 온다 (Batches 계약) — 뒤집어 돌려주어 순서 복원을 강제한다.
        for custom_id, text in reversed(list(self.answers.items())):
            yield SimpleNamespace(
                custom_id=custom_id,
                result=SimpleNamespace(type="succeeded", message=_message(text)),
            )


class FakeMessages:
    def __init__(self, answers: list[str], batches: FakeBatches | None = None) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []
        self.batches = batches or FakeBatches({})

    def create(self, **params: Any) -> SimpleNamespace:
        self.calls.append(params)
        return _message(self.answers.pop(0))


class FakeClient:
    def __init__(self, answers: list[str], batches: FakeBatches | None = None) -> None:
        self.messages = FakeMessages(answers, batches)


def test_the_system_prompt_carries_the_contract_label_criteria_verbatim():
    """골드의 정의는 formats.md 에 있다 — 프롬프트가 그것을 고쳐 쓰면 다른 과제를 채점하게 된다."""
    assert f"- 라벨 기준(polarity): {LABEL_CRITERIA}" in FORMATS.read_text(encoding="utf-8")
    assert LABEL_CRITERIA in system_prompt(SUN)


def test_the_system_prompt_lists_the_dictionary_aspects_under_their_category():
    rendered = system_prompt(SUN)
    assert "선블록: 끈적유분, 백탁" in rendered
    assert "배송포장" in rendered
    assert "불만" in rendered and "만족" in rendered and "중립" in rendered


def test_the_user_message_carries_the_sentence_the_category_and_the_rating():
    rendered = user_prompt(SENTENCE, 1.0, "선블록")
    assert SENTENCE in rendered and "선블록" in rendered and "1.0" in rendered


def test_the_version_is_the_model_and_the_prompt_date_in_the_contract_shape():
    assert version_for("claude-sonnet-5") == f"llm-claude-sonnet-5-{PROMPT_DATE}"
    assert re.match(r"^llm-.+-\d{8}$", version_for(DEFAULT_MODEL))


def test_the_rule_implementation_answers_classify_many_by_repeating_classify():
    rule: Polarity = RulePolarity()
    items = (PolarityRequest(SENTENCE, 1.0, "선블록"), PolarityRequest("백탁 없어서 좋아요", 5.0, "선블록"))
    many = rule.classify_many(items, SUN)
    one_by_one = [rule.classify(x.sentence, x.rating, x.category, SUN) for x in items]
    assert [r.polarity for r in many] == [r.polarity for r in one_by_one]


@pytest.mark.postgres
class TestAgainstAFakeClient:
    def _polarity(self, conn: Any, client: FakeClient, **kwargs: Any) -> LLMPolarity:
        return LLMPolarity("claude-sonnet-5", UsageLedger(conn), client=client, **kwargs)

    def test_a_structured_answer_becomes_a_polarity_result_tagged_with_the_version(
        self, needs_runtime_url: str
    ):
        client = FakeClient([_answer("불만")])
        with connect(needs_runtime_url) as conn:
            found = self._polarity(conn, client).classify(SENTENCE, 1.0, "선블록", SUN)
        assert (found.aspect, found.polarity, found.reason) == ("끈적유분", "불만", "부정 경험")
        assert found.version == f"llm-claude-sonnet-5-{PROMPT_DATE}"

    def test_the_request_pins_the_three_labels_as_a_structured_output_schema(self, needs_runtime_url: str):
        client = FakeClient([_answer("중립")])
        with connect(needs_runtime_url) as conn:
            self._polarity(conn, client).classify(SENTENCE, None, None, SUN)
        schema = client.messages.calls[0]["output_config"]["format"]["schema"]
        assert schema["properties"]["polarity"]["enum"] == ["불만", "만족", "중립"]
        assert schema["additionalProperties"] is False

    def test_an_answer_outside_the_three_labels_is_retried_once_and_then_neutral(
        self, needs_runtime_url: str
    ):
        client = FakeClient([_answer("긍정"), _answer("애매")])
        with connect(needs_runtime_url) as conn:
            found = self._polarity(conn, client).classify(SENTENCE, None, "선블록", SUN)
        assert found.polarity == "중립"
        assert len(client.messages.calls) == 2  # 재시도는 한 번뿐이다
        assert "긍정" not in found.reason

    def test_a_retry_that_comes_back_inside_the_three_labels_is_kept(self, needs_runtime_url: str):
        client = FakeClient([_answer("긍정"), _answer("불만")])
        with connect(needs_runtime_url) as conn:
            found = self._polarity(conn, client).classify(SENTENCE, None, "선블록", SUN)
        assert found.polarity == "불만"

    def test_an_absent_aspect_stays_none_so_the_writer_can_store_the_empty_key(self, needs_runtime_url: str):
        client = FakeClient([_answer("중립", aspect=None)])
        with connect(needs_runtime_url) as conn:
            found = self._polarity(conn, client).classify("배송이 늦었어요", None, "선블록", SUN)
        assert found.aspect is None  # B8: 저장은 need_key='' 로 접힌다

    def test_an_aspect_outside_the_dictionary_is_dropped_rather_than_invented(self, needs_runtime_url: str):
        client = FakeClient([_answer("불만", aspect="가격만족도")])
        with connect(needs_runtime_url) as conn:
            found = self._polarity(conn, client).classify(SENTENCE, None, "선블록", SUN)
        assert found.aspect is None and found.polarity == "불만"

    def test_every_call_is_written_to_the_ledger_with_its_cost(self, needs_runtime_url: str):
        client = FakeClient([_answer("불만")])
        with connect(needs_runtime_url) as conn:
            ledger = UsageLedger(conn)
            LLMPolarity("claude-sonnet-5", ledger, client=client, purpose="eval:polarity").classify(
                SENTENCE, None, "선블록", SUN
            )
            with conn.cursor() as cur:
                cur.execute("SELECT model, purpose, input_tokens, output_tokens, usd FROM llm_usage")
                rows = cur.fetchall()
        # 100 x $3/1M + 50 x $15/1M
        assert rows == [("claude-sonnet-5", "eval:polarity", 100, 50, Decimal("0.00105"))]

    def test_the_budget_is_checked_before_the_call_and_not_after_the_money_is_gone(
        self, needs_runtime_url: str
    ):
        client = FakeClient([_answer("불만")])
        with connect(needs_runtime_url) as conn:
            ledger = UsageLedger(conn)
            ledger.record("claude-sonnet-5", "earlier", Usage(output_tokens=466_000))  # $6.99
            with pytest.raises(BudgetExceeded):
                LLMPolarity("claude-sonnet-5", ledger, client=client).classify(SENTENCE, None, None, SUN)
        assert client.messages.calls == []  # 호출 자체가 없었다

    def test_classify_many_submits_one_batch_and_returns_the_input_order(self, needs_runtime_url: str):
        batches = FakeBatches({"p0": _answer("불만"), "p1": _answer("만족", aspect="백탁")})
        client = FakeClient([], batches)
        items = (PolarityRequest(SENTENCE, 1.0, "선블록"), PolarityRequest("백탁 없어요", 5.0, "선블록"))
        with connect(needs_runtime_url) as conn:
            found = LLMPolarity("claude-sonnet-5", UsageLedger(conn), client=client).classify_many(items, SUN)
        assert [r.polarity for r in found] == ["불만", "만족"]
        assert len(batches.submitted) == 1 and len(batches.submitted[0]) == 2

    def test_a_batch_records_its_usage_against_the_batch_id_at_batch_prices(self, needs_runtime_url: str):
        batches = FakeBatches({"p0": _answer("불만")})
        client = FakeClient([], batches)
        with connect(needs_runtime_url) as conn:
            LLMPolarity("claude-sonnet-5", UsageLedger(conn), client=client).classify_many(
                (PolarityRequest(SENTENCE, 1.0, "선블록"),), SUN
            )
            with conn.cursor() as cur:
                cur.execute("SELECT batch_id, usd FROM llm_usage")
                rows = cur.fetchall()
        assert rows == [("msgbatch_fake", Decimal("0.000525"))]  # 단건 $0.00105 의 절반

    def test_a_batch_result_that_failed_falls_back_to_neutral_rather_than_shifting_the_rest(
        self, needs_runtime_url: str
    ):
        batches = FakeBatches({"p0": _answer("불만")})  # p1 의 결과가 아예 오지 않는다
        client = FakeClient([], batches)
        items = (PolarityRequest(SENTENCE, 1.0, "선블록"), PolarityRequest("백탁 없어요", 5.0, "선블록"))
        with connect(needs_runtime_url) as conn:
            found = LLMPolarity("claude-sonnet-5", UsageLedger(conn), client=client).classify_many(items, SUN)
        assert [r.polarity for r in found] == ["불만", "중립"]
