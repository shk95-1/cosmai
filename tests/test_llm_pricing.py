"""The price table and the LLM_BUDGET_USD hard stop. The only machine check with money on it, so a fake usage
measures the running total and the block together."""

from __future__ import annotations

from decimal import Decimal

import pytest

from analysis.polarity.pricing import (
    LLM_BUDGET_USD,
    PRICES,
    PRICES_SOURCE_DATE,
    BudgetExceeded,
    Reservation,
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
            # The token count is derived from the constant so that a changed LLM_BUDGET_USD cannot make this
            # pass falsely: once spending has come to just under the budget, what is left cannot start a call
            # of one million output tokens.
            sonnet_output_rate = PRICES["claude-sonnet-5"].output_usd
            tokens_just_under_budget = int(LLM_BUDGET_USD / sonnet_output_rate * ONE_MILLION) - 1
            ledger.record("claude-sonnet-5", "earlier", Usage(output_tokens=tokens_just_under_budget))
            spent = ledger.spent()
            assert spent < LLM_BUDGET_USD
            with pytest.raises(BudgetExceeded) as blocked:
                ledger.reserve("claude-sonnet-5", "eval", Usage(output_tokens=ONE_MILLION))
            assert f"{LLM_BUDGET_USD:.2f}" in str(blocked.value)
            # A refusal does not grow the ledger — because there was no call.
            assert ledger.spent() == spent

    def test_a_call_that_fits_in_what_is_left_is_let_through(self, needs_runtime_url: str):
        with connect(needs_runtime_url) as conn:
            ledger = UsageLedger(conn)
            ledger.record("claude-sonnet-5", "earlier", Usage(output_tokens=466_000))
            assert ledger.reserve("claude-sonnet-5", "eval", Usage(output_tokens=100)).usd == Decimal(
                "0.0015"
            )

    def test_a_reservation_counts_against_the_budget_before_any_result_comes_back(
        self, needs_runtime_url: str
    ):
        """A timeout or a Ctrl-C with no response is still billed — the reservation has to stay for the next
        run to see it."""
        with connect(needs_runtime_url) as conn:
            # A narrow budget independent of LLM_BUDGET_USD keeps the boundary check meaningful whatever that
            # constant is.
            ledger = UsageLedger(conn, budget=Decimal("7.00"))
            ledger.reserve("claude-sonnet-5", "eval", Usage(output_tokens=400_000))  # $6.00
            assert ledger.spent() == Decimal("6.00")
            with pytest.raises(BudgetExceeded):
                ledger.reserve("claude-sonnet-5", "eval", Usage(output_tokens=100_000))  # $1.50 > $1.00 left

    def test_settling_replaces_the_reservation_instead_of_adding_a_second_row(self, needs_runtime_url: str):
        with connect(needs_runtime_url) as conn:
            ledger = UsageLedger(conn)
            reserved = ledger.reserve(
                "claude-sonnet-5", "eval", Usage(output_tokens=400_000), batch=True, batch_id="b1"
            )
            assert ledger.spent() == Decimal("3.00")
            ledger.settle(reserved, "eval done", Usage(output_tokens=1_000), batch_id="b1")
            with conn.cursor() as cur:
                cur.execute("SELECT purpose, usd, batch_id FROM llm_usage")
                rows = cur.fetchall()
            assert rows == [("eval done", Decimal("0.0075"), "b1")]  # 1000 x $15/1M x 0.5
            assert ledger.spent() == Decimal("0.0075")

    def test_a_reservation_is_found_again_by_its_batch_id(self, needs_runtime_url: str):
        """Settlement attaches even when submit and collect are different runs — batch_id is that address."""
        with connect(needs_runtime_url) as conn:
            ledger = UsageLedger(conn)
            made = ledger.reserve("claude-opus-5", "eval", Usage(output_tokens=10), batch=True)
            ledger.attach_batch_id(made, "msgbatch_z")
            found = ledger.reservation_for("msgbatch_z")
            assert isinstance(found, Reservation)
            assert (found.id, found.model, found.batch) == (made.id, "claude-opus-5", True)
            assert ledger.reservation_for("msgbatch_absent") is None

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
