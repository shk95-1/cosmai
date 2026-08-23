"""가격표와 $7 하드스톱. 돈이 걸린 유일한 기계 검사이므로 가짜 usage 로 누적과 차단을 함께 잰다."""

from __future__ import annotations

from decimal import Decimal

import pytest

from analysis.polarity.pricing import (
    LLM_BUDGET_USD,
    PRICES,
    PRICES_SOURCE_DATE,
    BudgetExceeded,
    Usage,
    UsageLedger,
    budget_remaining,
    cost_usd,
    price_for,
)
from db.seed._common import connect

ONE_MILLION = 1_000_000


def test_the_two_models_the_comparison_run_uses_are_priced_and_the_table_is_dated():
    assert {"claude-sonnet-5", "claude-opus-5"} <= set(PRICES)
    assert PRICES_SOURCE_DATE == "2026-08-24"


@pytest.mark.parametrize("model", sorted(PRICES))
def test_cache_read_and_write_are_the_documented_multiples_of_the_input_rate(model: str):
    price = PRICES[model]
    assert price.cache_read_usd == price.input_usd / 10
    assert price.cache_write_usd == price.input_usd * Decimal("1.25")


def test_the_cost_is_the_four_token_buckets_at_their_own_rates():
    usage = Usage(input_tokens=ONE_MILLION, output_tokens=ONE_MILLION, cache_read=0, cache_write=0)
    assert cost_usd("claude-sonnet-5", usage) == Decimal("18.00")
    assert cost_usd("claude-opus-5", usage) == Decimal("30.00")
    cached = Usage(cache_read=ONE_MILLION, cache_write=ONE_MILLION)
    assert cost_usd("claude-sonnet-5", cached) == Decimal("4.05")


def test_a_batch_costs_half_of_the_same_tokens_sent_one_at_a_time():
    usage = Usage(input_tokens=ONE_MILLION, output_tokens=ONE_MILLION)
    assert cost_usd("claude-sonnet-5", usage, batch=True) == cost_usd("claude-sonnet-5", usage) / 2


def test_an_unpriced_model_is_refused_rather_than_billed_at_zero():
    with pytest.raises(LookupError) as refused:
        price_for("claude-something-6")
    assert "claude-something-6" in str(refused.value)


def test_a_local_model_is_free_so_the_plumbing_never_eats_the_budget():
    assert cost_usd("ollama:gemma4:latest", Usage(input_tokens=ONE_MILLION)) == Decimal(0)


@pytest.mark.postgres
class TestTheLedger:
    def test_it_sums_what_was_recorded_and_reports_what_is_left(self, needs_runtime_url: str):
        with connect(needs_runtime_url) as conn:
            ledger = UsageLedger(conn)
            assert ledger.spent() == Decimal(0)
            ledger.record("claude-sonnet-5", "eval:polarity", Usage(input_tokens=ONE_MILLION))
            ledger.record("claude-sonnet-5", "eval:polarity", Usage(output_tokens=ONE_MILLION))
            assert ledger.spent() == Decimal("18.00")
            assert ledger.remaining() == LLM_BUDGET_USD - Decimal("18.00")
            assert budget_remaining(conn) == ledger.remaining()

    def test_the_hard_stop_refuses_before_the_call_that_would_cross_the_budget(self, needs_runtime_url: str):
        with connect(needs_runtime_url) as conn:
            ledger = UsageLedger(conn)
            # $6.99 이미 씀: 남은 $0.01 로는 100만 출력 토큰짜리 호출을 시작할 수 없다.
            ledger.record("claude-sonnet-5", "earlier", Usage(output_tokens=466_000))
            assert ledger.spent() == Decimal("6.99")
            with pytest.raises(BudgetExceeded) as blocked:
                ledger.check("claude-sonnet-5", Usage(output_tokens=ONE_MILLION))
            assert "7.00" in str(blocked.value)
            # 차단은 원장을 늘리지 않는다 — 호출이 없었기 때문이다.
            assert ledger.spent() == Decimal("6.99")

    def test_a_call_that_fits_in_what_is_left_is_let_through(self, needs_runtime_url: str):
        with connect(needs_runtime_url) as conn:
            ledger = UsageLedger(conn)
            ledger.record("claude-sonnet-5", "earlier", Usage(output_tokens=466_000))
            assert ledger.check("claude-sonnet-5", Usage(output_tokens=100)) == Decimal("0.0015")

    def test_every_recorded_row_keeps_its_tokens_its_purpose_and_its_batch_id(self, needs_runtime_url: str):
        with connect(needs_runtime_url) as conn:
            UsageLedger(conn).record(
                "claude-opus-5",
                "eval:polarity:sun holdout 100",
                Usage(input_tokens=11, output_tokens=22, cache_read=33, cache_write=44),
                batch=True,
                batch_id="msgbatch_x",
            )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT model, purpose, input_tokens, output_tokens, cache_read, cache_write, "
                    "usd, batch_id FROM llm_usage"
                )
                rows = cur.fetchall()
        assert rows == [
            (
                "claude-opus-5",
                "eval:polarity:sun holdout 100",
                11,
                22,
                33,
                44,
                Decimal("0.00044825"),
                "msgbatch_x",
            )
        ]
