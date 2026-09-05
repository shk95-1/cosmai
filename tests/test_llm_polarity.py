"""LLMPolarity: prompt, structured output, retry, batch, ledger. No real Anthropic call is here (a fake
client)."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from analysis.lexicon import DISCOURSE_MARKERS, WISH_MARKERS
from analysis.polarity import RulePolarity
from analysis.polarity.llm import DEFAULT_MODEL, PROMPT_DATE, TRUNCATED, LLMPolarity, version_for
from analysis.polarity.pricing import BudgetExceeded, PurposeCap, Usage, UsageLedger
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


def _message(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], usage=USAGE, stop_reason=stop_reason
    )


COUNTS = SimpleNamespace(succeeded=1, errored=0, expired=0, canceled=0, processing=0)


class FakeBatches:
    def __init__(
        self, answers: dict[str, str], failures: dict[str, str] | None = None, status: str = "ended"
    ) -> None:
        self.answers = answers
        self.failures = failures or {}
        self.status = status
        self.submitted: list[Any] = []
        self.polls = 0

    def create(self, *, requests: list[Any]) -> SimpleNamespace:
        self.submitted.append(requests)
        return SimpleNamespace(id="msgbatch_fake")

    def retrieve(self, batch_id: str) -> SimpleNamespace:
        self.polls += 1
        return SimpleNamespace(id=batch_id, processing_status=self.status, request_counts=COUNTS)

    def results(self, batch_id: str) -> Any:
        # Results arrive in any order (the Batches contract) — returning them reversed forces the order to be
        # restored.
        for custom_id, text in reversed(list(self.answers.items())):
            yield SimpleNamespace(
                custom_id=custom_id,
                result=SimpleNamespace(type="succeeded", message=_message(text)),
            )
        for custom_id, kind in self.failures.items():
            yield SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type=kind))


class FakeMessages:
    def __init__(self, answers: list[str], batches: FakeBatches | None = None) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []
        self.stop_reason = "end_turn"
        self.batches = batches or FakeBatches({})

    def create(self, **params: Any) -> SimpleNamespace:
        self.calls.append(params)
        return _message(self.answers.pop(0), self.stop_reason)


class FakeClient:
    def __init__(self, answers: list[str], batches: FakeBatches | None = None) -> None:
        self.messages = FakeMessages(answers, batches)


def test_the_system_prompt_carries_the_contract_label_criteria_verbatim():
    """The definition of the gold set is in formats.md — a prompt that rewrites it grades a different task."""
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


def _rows_for(conn, purpose: str) -> int:
    """How many ledger rows that purpose has in either state -- held (`reserve:p`) or settled (`p`)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM llm_usage WHERE purpose IN (%s, %s)", (purpose, f"reserve:{purpose}")
        )
        row = cur.fetchone()
    conn.rollback()
    return int(row[0]) if row else 0


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
        assert len(client.messages.calls) == 2  # the retry happens only once
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
        assert found.aspect is None  # B8: storage folds it to need_key=''

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
            # A narrow budget independent of LLM_BUDGET_USD keeps the boundary check meaningful whatever that
            # constant is.
            ledger = UsageLedger(conn, budget=Decimal("7.00"))
            ledger.record("claude-sonnet-5", "earlier", Usage(output_tokens=466_600))  # $6.999, $0.001 left
            with pytest.raises(BudgetExceeded):
                LLMPolarity("claude-sonnet-5", ledger, client=client).classify(SENTENCE, None, None, SUN)
        assert client.messages.calls == []  # there was no call at all

    def test_a_purpose_cap_refuses_an_estimate_over_its_per_call_ceiling(self, needs_runtime_url: str):
        """A cap belongs to one purpose and is read where the global stop is read -- under the same
        lock, before the reservation row, so a refusal leaves the ledger as it found it."""
        with connect(needs_runtime_url) as conn:
            ledger = UsageLedger(conn, caps={"p": PurposeCap(per_call=Decimal("0.01"))})
            with pytest.raises(BudgetExceeded, match="per-call cap"):
                ledger.reserve("claude-sonnet-5", "p", Usage(output_tokens=1000))  # 1000 x $15/1M = $0.015
            assert _rows_for(conn, "p") == 0

    def test_a_per_day_cap_counts_todays_held_and_settled_rows_of_that_purpose(self, needs_runtime_url: str):
        """settle overwrites the reservation row, so a call is one row under `reserve:p` or under
        `p` -- summing both is the day's spend, not the same money twice."""
        with connect(needs_runtime_url) as conn:
            ledger = UsageLedger(conn, caps={"p": PurposeCap(per_day=Decimal("0.05"))})
            ledger.record("claude-sonnet-5", "p", Usage(output_tokens=2_000))  # $0.030 settled
            ledger.record("claude-sonnet-5", "reserve:p", Usage(output_tokens=1_000))  # $0.015 held
            with pytest.raises(BudgetExceeded, match="per-day cap"):
                ledger.reserve("claude-sonnet-5", "p", Usage(output_tokens=500))  # $0.0075, over $0.05
            assert _rows_for(conn, "p") == 2  # nothing was reserved on the way to the refusal

    def test_yesterdays_spend_does_not_count_against_todays_per_day_cap(self, needs_runtime_url: str):
        with connect(needs_runtime_url) as conn:
            ledger = UsageLedger(conn, caps={"p": PurposeCap(per_day=Decimal("0.05"))})
            ledger.record("claude-sonnet-5", "p", Usage(output_tokens=2_000))
            ledger.record("claude-sonnet-5", "reserve:p", Usage(output_tokens=1_000))
            with conn.cursor() as cur:
                cur.execute("UPDATE llm_usage SET called_at = now() - interval '1 day'")
            conn.commit()
            # The same call the test above refuses: with the $0.045 moved off today it is allowed.
            assert ledger.reserve("claude-sonnet-5", "p", Usage(output_tokens=500)).usd == Decimal("0.0075")

    def test_the_per_day_window_is_the_utc_day_whatever_the_session_time_zone_says(
        self, needs_runtime_url: str
    ):
        """`SPENT_TODAY_BY_PURPOSE` truncates in UTC on purpose; a naive `date_trunc('day', now())` would be
        the session's day -- nine hours off in Asia/Seoul -- and no test saw it (fork #86). The probe row
        sits one minute after the earlier of the two midnights, so at any wall-clock time exactly one of the
        two readings counts it, and the UTC reading counts it iff that earlier midnight is UTC's."""
        with connect(needs_runtime_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TimeZone = 'Asia/Seoul'")
                cur.execute(
                    "SELECT date_trunc('day', now() AT TIME ZONE 'utc') AT TIME ZONE 'utc',"
                    " date_trunc('day', now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul'"
                )
                utc_midnight, seoul_midnight = cur.fetchone()  # type: ignore[misc]
            conn.commit()
            probe = min(utc_midnight, seoul_midnight) + timedelta(minutes=1)
            counted_in_utc = probe >= utc_midnight
            ledger = UsageLedger(conn, caps={"p": PurposeCap(per_day=Decimal("0.05"))})
            ledger.record("claude-sonnet-5", "p", Usage(output_tokens=2_000))  # $0.030
            ledger.record("claude-sonnet-5", "reserve:p", Usage(output_tokens=1_000))  # $0.015
            with conn.cursor() as cur:
                cur.execute("UPDATE llm_usage SET called_at = %s", (probe,))
            conn.commit()
            if counted_in_utc:
                with pytest.raises(BudgetExceeded, match="per-day cap"):
                    ledger.reserve("claude-sonnet-5", "p", Usage(output_tokens=500))
            else:
                assert ledger.reserve("claude-sonnet-5", "p", Usage(output_tokens=500)).usd == Decimal(
                    "0.0075"
                )

    def test_a_purpose_with_no_cap_is_not_charged_another_purposes_cap(self, needs_runtime_url: str):
        with connect(needs_runtime_url) as conn:
            caps = {"p": PurposeCap(per_call=Decimal("0.001"), per_day=Decimal("0.001"))}
            ledger = UsageLedger(conn, caps=caps)
            assert ledger.reserve("claude-sonnet-5", "q", Usage(output_tokens=1_000)).usd == Decimal("0.015")

    def test_the_ledger_is_a_required_argument_so_no_call_can_skip_the_hard_stop(self):
        with pytest.raises(TypeError):
            LLMPolarity("claude-sonnet-5")  # type: ignore[call-arg]

    def test_a_truncated_answer_says_so_instead_of_looking_like_a_label_violation(
        self, needs_runtime_url: str
    ):
        client = FakeClient([_answer("긍정"), _answer("긍정")])
        client.messages.stop_reason = "max_tokens"
        with connect(needs_runtime_url) as conn:
            found = self._polarity(conn, client).classify(SENTENCE, None, "선블록", SUN)
        assert found.reason == TRUNCATED

    def test_the_batch_is_reserved_before_submission_and_names_its_recovery_address(
        self, needs_runtime_url: str
    ):
        batches = FakeBatches({"p0": _answer("불만")}, status="in_progress")
        client = FakeClient([], batches)
        with connect(needs_runtime_url) as conn:
            ledger = UsageLedger(conn)
            llm = LLMPolarity("claude-sonnet-5", ledger, client=client, timeout_seconds=0.0, poll_seconds=0.0)
            with pytest.raises(LookupError) as timed_out:
                llm.classify_many((PolarityRequest(SENTENCE, 1.0, "선블록"),), SUN)
            assert "msgbatch_fake" in str(timed_out.value)
            # The reservation stays even when no response arrives — the next run's budget sees it.
            assert ledger.spent() > 0
            assert ledger.reservation_for("msgbatch_fake") is not None

    def test_a_failed_batch_item_records_which_failure_made_it_neutral(self, needs_runtime_url: str):
        batches = FakeBatches({"p0": _answer("불만")}, failures={"p1": "expired"})
        client = FakeClient([], batches)
        items = (PolarityRequest(SENTENCE, 1.0, "선블록"), PolarityRequest("백탁 없어요", 5.0, "선블록"))
        with connect(needs_runtime_url) as conn:
            found = LLMPolarity("claude-sonnet-5", UsageLedger(conn), client=client).classify_many(items, SUN)
            with conn.cursor() as cur:
                cur.execute("SELECT purpose FROM llm_usage")
                purposes = [r[0] for r in cur.fetchall()]
        assert found[1].reason == "llm:배치 expired"
        assert "succeeded:1" in purposes[0]  # how many became neutral and why stays in the ledger

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
        assert rows == [("msgbatch_fake", Decimal("0.000525"))]  # half of the $0.00105 single call

    def test_a_batch_result_that_failed_falls_back_to_neutral_rather_than_shifting_the_rest(
        self, needs_runtime_url: str
    ):
        batches = FakeBatches({"p0": _answer("불만")})  # p1 의 결과가 아예 오지 않는다
        client = FakeClient([], batches)
        items = (PolarityRequest(SENTENCE, 1.0, "선블록"), PolarityRequest("백탁 없어요", 5.0, "선블록"))
        with connect(needs_runtime_url) as conn:
            found = LLMPolarity("claude-sonnet-5", UsageLedger(conn), client=client).classify_many(items, SUN)
        assert [r.polarity for r in found] == ["불만", "중립"]
