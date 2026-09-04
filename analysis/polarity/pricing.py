"""Per-model rates and the $10.00 hard stop. The running total is needs.llm_usage (DDL 003) and the block is
*before* the call.

Counting after the call does not bring back money that has already gone out. So reserve() locks, reads,
writes an estimate row and commits inside one transaction: even when no response follows (timeout, Ctrl-C,
exception) the reservation stays in the ledger and comes off the next run's budget. settle() overwrites that
row with the measurement — adding a new row would count it twice.

Without the lock two runs read the same remaining() and both submit. pg_advisory_xact_lock puts the read and
the reservation into a single line.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, LiteralString

import psycopg

# Source: claude-api skill §Current Models (cached 2026-06-24) as read on 2026-08-24. $ per 1M tokens.
# cache read = input x 0.1, cache write (5-minute ephemeral) = input x 1.25 (same skill §Prompt Caching).
# Sonnet 5 is at the intro $2/$10 until 2026-08-31, but the hard stop counts list price — better early
# than late.
PRICES_SOURCE_DATE = "2026-08-24"
PER_MILLION = Decimal(1_000_000)
BATCH_DISCOUNT = Decimal("0.5")  # The Batches API is 50% on every token
# 선블록(lexicon_category='선블록') 9,653문장 전량이 Batches 로 캐시 없이 $17.8·캐시 있으면 $6.0
# (2026-08-24 프로브 5회 실측 단가) — $7 은 캐시 의존 여유가 $1뿐이라 사용자가 $10 으로 승인.
LLM_BUDGET_USD = Decimal("10.00")  # contracts/secrets.md · approved up front in #6, $10 approved 2026-08-24
OLLAMA_PREFIX = "ollama:"


@dataclass(frozen=True)
class Price:
    input_usd: Decimal
    output_usd: Decimal
    cache_read_usd: Decimal
    cache_write_usd: Decimal


def _price(input_usd: str, output_usd: str) -> Price:
    rate = Decimal(input_usd)
    return Price(rate, Decimal(output_usd), rate / 10, rate * Decimal("1.25"))


PRICES: dict[str, Price] = {
    "claude-opus-5": _price("5.00", "25.00"),
    "claude-sonnet-5": _price("3.00", "15.00"),
    "claude-haiku-4-5": _price("1.00", "5.00"),
}
FREE = Price(Decimal(0), Decimal(0), Decimal(0), Decimal(0))


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0


class BudgetExceeded(RuntimeError):
    """The hard stop. At the moment this exception is raised the call has not gone out yet."""


def price_for(model: str) -> Price:
    if model.startswith(OLLAMA_PREFIX):
        return FREE  # A local model is free — it goes in the ledger but eats no budget
    price = PRICES.get(model)
    if price is None:
        raise LookupError(
            f"{model} has no price in analysis/polarity/pricing.py (source {PRICES_SOURCE_DATE}); "
            "add its four rates before spending money on it"
        )
    return price


def cost_usd(model: str, usage: Usage, *, batch: bool = False) -> Decimal:
    price = price_for(model)
    total = (
        usage.input_tokens * price.input_usd
        + usage.output_tokens * price.output_usd
        + usage.cache_read * price.cache_read_usd
        + usage.cache_write * price.cache_write_usd
    ) / PER_MILLION
    return total * BATCH_DISCOUNT if batch else total


# The issue number is the lock key — one run at a time passes through reading the budget and reserving.
ADVISORY_KEY = 6
LOCK: LiteralString = "SELECT pg_advisory_xact_lock(%s)"
SPENT: LiteralString = "SELECT coalesce(sum(usd), 0) FROM llm_usage"
RECORD: LiteralString = """
INSERT INTO llm_usage (model, purpose, input_tokens, output_tokens, cache_read, cache_write, usd, batch_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
"""
SETTLE: LiteralString = """
UPDATE llm_usage SET called_at = now(), purpose = %s, input_tokens = %s, output_tokens = %s,
       cache_read = %s, cache_write = %s, usd = %s, batch_id = coalesce(%s, batch_id)
WHERE id = %s
"""
RESERVED: LiteralString = "SELECT id, model, usd FROM llm_usage WHERE batch_id = %s ORDER BY id LIMIT 1"


@dataclass(frozen=True)
class Reservation:
    """One estimate row held in the ledger before submission. settle() overwrites it once the measurement
    arrives."""

    id: int
    model: str
    usd: Decimal
    batch: bool


class UsageLedger:
    def __init__(self, conn: psycopg.Connection[Any], *, budget: Decimal = LLM_BUDGET_USD) -> None:
        self.conn = conn
        self.budget = budget

    def spent(self) -> Decimal:
        with self.conn.cursor() as cur:
            cur.execute(SPENT)
            row = cur.fetchone()
        # Closed as soon as it is read: held open during a decision, idle_in_transaction 15s cuts the session.
        self.conn.rollback()
        return Decimal(row[0]) if row else Decimal(0)

    def remaining(self) -> Decimal:
        return self.budget - self.spent()

    def reserve(
        self,
        model: str,
        purpose: str,
        usage: Usage,
        *,
        batch: bool = False,
        batch_id: str | None = None,
    ) -> Reservation:
        """Lock → read the total → check the estimate → reservation row → commit, all one transaction. The
        call goes out after this."""
        usd = cost_usd(model, usage, batch=batch)
        with self.conn.cursor() as cur:
            cur.execute(LOCK, (ADVISORY_KEY,))
            cur.execute(SPENT)
            row = cur.fetchone()
            left = self.budget - (Decimal(row[0]) if row else Decimal(0))
            if usd > left:
                self.conn.rollback()
                raise BudgetExceeded(
                    f"{model}: this call is estimated at ${usd:.4f} and only ${left:.4f} of the "
                    f"${self.budget:.2f} budget is left (needs.llm_usage)"
                )
            cur.execute(
                RECORD,
                (
                    model,
                    f"reserve:{purpose}",
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_read,
                    usage.cache_write,
                    usd,
                    batch_id,
                ),
            )
            reserved = cur.fetchone()
        self.conn.commit()
        return Reservation(id=int(reserved[0]) if reserved else 0, model=model, usd=usd, batch=batch)

    def settle(
        self,
        reservation: Reservation,
        purpose: str,
        usage: Usage,
        *,
        batch_id: str | None = None,
    ) -> Decimal:
        """Overwrites the reservation row with the measurement. Adding a row would leave the reservation in
        place and take the budget twice."""
        usd = cost_usd(reservation.model, usage, batch=reservation.batch)
        with self.conn.cursor() as cur:
            cur.execute(
                SETTLE,
                (
                    purpose,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_read,
                    usage.cache_write,
                    usd,
                    batch_id,
                    reservation.id,
                ),
            )
        self.conn.commit()
        return usd

    def reservation_for(self, batch_id: str) -> Reservation | None:
        """Finds the reservation row again by batch_id — settlement attaches even when submit and collect are
        different runs."""
        with self.conn.cursor() as cur:
            cur.execute(RESERVED, (batch_id,))
            row = cur.fetchone()
        self.conn.rollback()
        if row is None:
            return None
        return Reservation(id=int(row[0]), model=str(row[1]), usd=Decimal(row[2]), batch=True)

    def attach_batch_id(self, reservation: Reservation, batch_id: str) -> None:
        """Attaches the collection address to the reservation row right after submission — it is the only key
        that gets the results back for 29 days."""
        with self.conn.cursor() as cur:
            cur.execute("UPDATE llm_usage SET batch_id = %s WHERE id = %s", (batch_id, reservation.id))
        self.conn.commit()

    def record(
        self,
        model: str,
        purpose: str,
        usage: Usage,
        *,
        batch: bool = False,
        batch_id: str | None = None,
    ) -> Decimal:
        """Writes down spending that already happened (a free local model, a test's advance credit). No budget
        check."""
        usd = cost_usd(model, usage, batch=batch)
        with self.conn.cursor() as cur:
            cur.execute(
                RECORD,
                (
                    model,
                    purpose,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_read,
                    usage.cache_write,
                    usd,
                    batch_id,
                ),
            )
        self.conn.commit()
        return usd


def budget_remaining(conn: psycopg.Connection[Any], budget: Decimal = LLM_BUDGET_USD) -> Decimal:
    return UsageLedger(conn, budget=budget).remaining()
